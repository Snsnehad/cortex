"""Registration, login, token refresh, logout and account changes.

Security properties (TASK-03.04):

* Registration creates user + organization + OWNER membership in ONE transaction. A duplicate
  email (any letter case) is refused by the database's unique index, so two simultaneous
  sign-ups with the same email cannot both succeed.
* Login answers unknown email, wrong password and deactivated account with the same error,
  and spends the same time on all three (a dummy hash verification for unknown emails), so it
  cannot be used to find out which emails have accounts.
* A successful login upgrades a password hash made with outdated parameters.
* Changing the password signs out every *other* session (stolen sessions die with the old
  password); the session making the change stays signed in.
"""

import re
import secrets
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError, ValidationFailedError
from app.core.passwords import PasswordHasher, validate_password
from app.core.refresh_tokens import InvalidRefreshTokenError, hash_refresh_token
from app.core.tokens import AccessToken, AccessTokenService
from app.models import Organization, OrganizationMembership, OrgRole, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.services.sessions import IssuedRefreshToken, SessionService

EMAIL_UNIQUE_INDEX = "uq_users_email_lower"
SLUG_UNIQUE_CONSTRAINT = "uq_organizations_slug"
SLUG_MAX_LENGTH = 60
_SLUG_ATTEMPTS = 3


class InvalidCredentialsError(AuthenticationError):
    default_code = "INVALID_CREDENTIALS"
    default_message = "Incorrect email or password."


@dataclass(frozen=True, slots=True)
class SignedIn:
    user: User
    access: AccessToken
    refresh: IssuedRefreshToken


@dataclass(frozen=True, slots=True)
class Registration(SignedIn):
    organization: Organization


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _violated_constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def slugify(name: str) -> str:
    """'Acme Corp. (EU)' -> 'acme-corp-eu'. ASCII only, safe in URLs."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    # Apostrophes join words ("Alice's" -> "alices"), they do not separate them.
    ascii_name = ascii_name.replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-") or "org"


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        hasher: PasswordHasher,
        tokens: AccessTokenService,
        refresh_ttl_days: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session = session
        self._hasher = hasher
        self._tokens = tokens
        self._users = UserRepository(session)
        self._organizations = OrganizationRepository(session)
        self._sessions = SessionService(session, ttl_days=refresh_ttl_days, clock=clock)
        self._clock = clock

    # --- registration ---------------------------------------------------------------------------

    async def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str,
        organization_name: str | None,
        user_agent: str | None,
    ) -> Registration:
        validate_password(password, email=email)
        if await self._users.get_by_email(email) is not None:
            raise self._email_taken()

        user = User(
            email=email,
            password_hash=await self._hasher.hash_async(password),
            full_name=full_name,
        )
        self._users.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # The pre-check above lost a race with a concurrent sign-up: the index decides.
            await self._session.rollback()
            if _violated_constraint(exc) == EMAIL_UNIQUE_INDEX:
                raise self._email_taken() from exc
            raise

        organization = await self._create_organization(
            organization_name or f"{full_name}'s workspace", creator=user
        )
        self._organizations.add_membership(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=OrgRole.OWNER
            )
        )
        await self._session.commit()

        refresh = await self._sessions.start(user.id, user_agent=user_agent)
        return Registration(
            user=user,
            organization=organization,
            access=self._tokens.issue(user.id),
            refresh=refresh,
        )

    @staticmethod
    def _email_taken() -> ConflictError:
        return ConflictError("An account with this email already exists.", code="EMAIL_TAKEN")

    async def _create_organization(self, name: str, *, creator: User) -> Organization:
        base = slugify(name)
        candidate = base
        if await self._organizations.slug_exists(candidate):
            candidate = f"{base}-{secrets.token_hex(3)}"
        for _ in range(_SLUG_ATTEMPTS):
            organization = Organization(name=name, slug=candidate, created_by_user_id=creator.id)
            try:
                # Savepoint: a slug collision (concurrent registration) rolls back only this
                # insert, not the user created earlier in the same transaction.
                async with self._session.begin_nested():
                    self._organizations.add(organization)
                    await self._session.flush()
            except IntegrityError as exc:
                if _violated_constraint(exc) != SLUG_UNIQUE_CONSTRAINT:
                    raise
                candidate = f"{base}-{secrets.token_hex(3)}"
                continue
            return organization
        raise ConflictError("Could not allocate an organization address. Please retry.")

    # --- sessions -------------------------------------------------------------------------------

    async def login(self, *, email: str, password: str, user_agent: str | None) -> SignedIn:
        user = await self._users.get_by_email(email)
        if user is None:
            await self._hasher.dummy_verify_async(password)  # same cost as a real check
            raise InvalidCredentialsError

        result = await self._hasher.verify_async(password, user.password_hash)
        # Inactive accounts get the same answer as a wrong password (checked after the hash
        # verification, so timing is identical too).
        if not result.valid or not user.is_active:
            raise InvalidCredentialsError
        if result.new_hash is not None:
            user.password_hash = result.new_hash
        user.last_login_at = self._clock()
        await self._session.commit()

        refresh = await self._sessions.start(user.id, user_agent=user_agent)
        return SignedIn(user=user, access=self._tokens.issue(user.id), refresh=refresh)

    async def refresh(
        self, raw_refresh_token: str | None, *, user_agent: str | None
    ) -> tuple[AccessToken, IssuedRefreshToken]:
        if not raw_refresh_token:
            raise InvalidRefreshTokenError
        issued = await self._sessions.rotate(raw_refresh_token, user_agent=user_agent)
        return self._tokens.issue(issued.user_id), issued

    async def logout(self, raw_refresh_token: str | None) -> None:
        if raw_refresh_token:
            await self._sessions.end(raw_refresh_token)

    # --- account ---------------------------------------------------------------------------------

    async def memberships(self, user: User) -> list[OrganizationMembership]:
        return await self._organizations.memberships_of(user.id)

    async def update_profile(self, user: User, *, full_name: str) -> User:
        user.full_name = full_name
        await self._session.commit()
        return user

    async def change_password(
        self,
        user: User,
        *,
        current_password: str,
        new_password: str,
        current_refresh_token: str | None,
    ) -> None:
        if not (await self._hasher.verify_async(current_password, user.password_hash)).valid:
            raise ValidationFailedError(
                "The current password is incorrect.",
                code="INVALID_CURRENT_PASSWORD",
                details={
                    "fields": [
                        {
                            "loc": ["body", "current_password"],
                            "msg": "The current password is incorrect.",
                            "type": "invalid_current_password",
                        }
                    ]
                },
            )
        validate_password(new_password, email=user.email)
        user.password_hash = await self._hasher.hash_async(new_password)
        keep_family = await self._own_family(user.id, current_refresh_token)
        await self._session.commit()
        await self._sessions.end_all_for_user(user.id, keep_family=keep_family)

    async def _own_family(
        self, user_id: uuid.UUID, raw_refresh_token: str | None
    ) -> uuid.UUID | None:
        """The family of the caller's own active refresh token, if it really is theirs."""
        if not raw_refresh_token:
            return None
        token = await RefreshTokenRepository(self._session).get_by_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if token is None or token.user_id != user_id or token.revoked_at is not None:
            return None
        return token.family_id

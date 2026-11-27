"""Organizations and their members (TASK-03.06).

Membership rules (API_SPECIFICATION.md §2):

* Any member can see the organization and its member list, and can leave.
* ADMIN+ can add existing users, change MEMBER/ADMIN roles and remove MEMBERs/ADMINs.
* Only an OWNER can grant the OWNER role, or change/remove another owner.
* An organization always keeps at least one OWNER (``LAST_OWNER``).

Concurrency: every membership change first locks the organization row (``SELECT … FOR
UPDATE``) and then re-reads the actor's and the target's *current* roles. Without that, two
owners demoting each other at the same moment would each see "two owners", both succeed, and
leave the organization with none. The role in ``OrgContext`` was read before the lock and may
already be stale, so it is never trusted for these decisions.

ADR-020: removing a membership deletes that member's private conversations in the
organization (database cascade).
"""

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import CursorCodec, PageResult
from app.core.tenancy import OrganizationNotFoundError, OrgContext
from app.models import Organization, OrganizationMembership, OrgRole, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.pagination import KeysetOrder, PageRequest, fetch_page
from app.repositories.users import UserRepository

SLUG_UNIQUE_CONSTRAINT = "uq_organizations_slug"
MEMBERSHIP_UNIQUE_CONSTRAINT = "uq_organization_memberships_organization_id_user_id"
SLUG_MAX_LENGTH = 60
_SLUG_ATTEMPTS = 3

OLDEST_FIRST = KeysetOrder(
    OrganizationMembership.created_at, OrganizationMembership.id, descending=False
)


class UserNotFoundError(NotFoundError):
    default_code = "USER_NOT_FOUND"
    default_message = "No active account exists for that email address."


class MemberNotFoundError(NotFoundError):
    default_code = "MEMBER_NOT_FOUND"
    default_message = "That user is not a member of this organization."


class LastOwnerError(ConflictError):
    default_code = "LAST_OWNER"
    default_message = "An organization must keep at least one owner."


class AlreadyMemberError(ConflictError):
    default_code = "ALREADY_MEMBER"
    default_message = "That user is already a member of this organization."


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


@dataclass(frozen=True, slots=True)
class OrganizationWithRole:
    organization: Organization
    role: OrgRole


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._organizations = OrganizationRepository(session)
        self._users = UserRepository(session)

    # --- organizations --------------------------------------------------------------------------

    async def add_with_owner(self, name: str, *, owner: User) -> Organization:
        """Create an organization with ``owner`` as OWNER. Does NOT commit: registration uses
        it inside its own transaction."""
        organization = await self._insert_with_unique_slug(name, creator=owner)
        self._organizations.add_membership(
            OrganizationMembership(
                organization_id=organization.id, user_id=owner.id, role=OrgRole.OWNER
            )
        )
        return organization

    async def create(self, name: str, *, owner: User) -> OrganizationWithRole:
        organization = await self.add_with_owner(name, owner=owner)
        await self._session.commit()
        return OrganizationWithRole(organization, OrgRole.OWNER)

    async def _insert_with_unique_slug(self, name: str, *, creator: User) -> Organization:
        base = slugify(name)
        candidate = base
        if await self._organizations.slug_exists(candidate):
            candidate = f"{base}-{secrets.token_hex(3)}"
        for _ in range(_SLUG_ATTEMPTS):
            organization = Organization(name=name, slug=candidate, created_by_user_id=creator.id)
            try:
                # Savepoint: a slug collision with a concurrent request rolls back only this
                # insert, not the rest of the caller's transaction.
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

    async def list_mine(
        self, user: User, *, page: PageRequest, codec: CursorCodec
    ) -> PageResult[OrganizationMembership]:
        return await fetch_page(
            self._session,
            self._organizations.memberships_of_query(user.id),
            OLDEST_FIRST,
            page=page,
            scope=f"orgs:user={user.id}:joined:asc",
            codec=codec,
        )

    async def get(self, ctx: OrgContext) -> OrganizationWithRole:
        organization = await self._organizations.get(ctx.organization_id)
        if organization is None:  # deleted between the membership check and now
            raise OrganizationNotFoundError
        return OrganizationWithRole(organization, ctx.role)

    async def rename(self, ctx: OrgContext, *, name: str) -> OrganizationWithRole:
        """Rename only; the slug stays, so existing links keep working."""
        organization = await self._organizations.get(ctx.organization_id)
        if organization is None:
            raise OrganizationNotFoundError
        organization.name = name
        await self._session.commit()
        return OrganizationWithRole(organization, ctx.role)

    # --- members --------------------------------------------------------------------------------

    async def list_members(
        self, ctx: OrgContext, *, page: PageRequest, codec: CursorCodec
    ) -> PageResult[OrganizationMembership]:
        return await fetch_page(
            self._session,
            self._organizations.members_query(ctx.organization_id),
            OLDEST_FIRST,
            page=page,
            scope=f"members:org={ctx.organization_id}:joined:asc",
            codec=codec,
        )

    async def add_member(
        self, ctx: OrgContext, *, email: str, role: OrgRole
    ) -> OrganizationMembership:
        actor_role = await self._lock_and_current_role(ctx)
        if not actor_role.at_least(OrgRole.ADMIN):
            raise PermissionDeniedError(details={"required_role": OrgRole.ADMIN.value})
        if role is OrgRole.OWNER and actor_role is not OrgRole.OWNER:
            raise PermissionDeniedError("Only an owner can grant the OWNER role.")

        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            await self._session.rollback()
            raise UserNotFoundError
        membership = OrganizationMembership(
            organization_id=ctx.organization_id, user_id=user.id, role=role
        )
        self._organizations.add_membership(membership)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            if _violated_constraint(exc) == MEMBERSHIP_UNIQUE_CONSTRAINT:
                raise AlreadyMemberError from exc
            raise
        await self._session.commit()
        membership.user = user
        return membership

    async def change_role(
        self, ctx: OrgContext, *, user_id: uuid.UUID, role: OrgRole
    ) -> OrganizationMembership:
        actor_role = await self._lock_and_current_role(ctx)
        target = await self._target(ctx, user_id)
        self._authorize_change(actor_role, target, new_role=role)

        if target.role is OrgRole.OWNER and role is not OrgRole.OWNER:
            await self._ensure_another_owner(ctx.organization_id)
        target.role = role
        await self._session.commit()
        return target

    async def remove_member(self, ctx: OrgContext, *, user_id: uuid.UUID) -> None:
        actor_role = await self._lock_and_current_role(ctx)
        target = await self._target(ctx, user_id)
        is_self = target.user_id == ctx.user_id
        if not is_self:
            self._authorize_change(actor_role, target, new_role=None)

        if target.role is OrgRole.OWNER:
            await self._ensure_another_owner(ctx.organization_id)
        await self._session.delete(target)  # cascades to their conversations (ADR-020)
        await self._session.commit()

    async def load_user(self, membership: OrganizationMembership) -> None:
        """Explicitly load membership.user (relationships never load lazily, ADR lazy='raise')."""
        await self._session.refresh(membership, attribute_names=["user"])

    # --- helpers ----------------------------------------------------------------------------------

    async def _lock_and_current_role(self, ctx: OrgContext) -> OrgRole:
        """Take the per-organization lock, then return the actor's role as it is *now*."""
        if await self._organizations.lock(ctx.organization_id) is None:
            await self._session.rollback()
            raise OrganizationNotFoundError
        actor = await self._organizations.get_membership(ctx.organization_id, ctx.user_id)
        if actor is None:  # removed while this request was waiting for the lock
            await self._session.rollback()
            raise OrganizationNotFoundError
        return actor.role

    async def _target(self, ctx: OrgContext, user_id: uuid.UUID) -> OrganizationMembership:
        target = await self._organizations.get_membership(ctx.organization_id, user_id)
        if target is None:
            await self._session.rollback()
            raise MemberNotFoundError
        return target

    @staticmethod
    def _authorize_change(
        actor_role: OrgRole,
        target: OrganizationMembership,
        *,
        new_role: OrgRole | None,
    ) -> None:
        """Rules for changing (new_role given) or removing (None) someone's membership."""
        if not actor_role.at_least(OrgRole.ADMIN):
            raise PermissionDeniedError(details={"required_role": OrgRole.ADMIN.value})
        involves_owner = target.role is OrgRole.OWNER or new_role is OrgRole.OWNER
        if involves_owner and actor_role is not OrgRole.OWNER:
            raise PermissionDeniedError("Only an owner can manage owners.")

    async def _ensure_another_owner(self, organization_id: uuid.UUID) -> None:
        if await self._organizations.count_owners(organization_id) <= 1:
            await self._session.rollback()
            raise LastOwnerError

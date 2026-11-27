"""Refresh-token sessions: issue, rotate with reuse detection, revoke (ADR-006, ADR-022).

A *family* is one login: the first refresh token and every token rotated from it share a
``family_id``. Each refresh replaces the presented token with a new one. Presenting a token
that was already replaced or revoked means two parties hold the same token, one of them
probably an attacker, so the **whole family is revoked**: the attacker and the legitimate
user both have to sign in again, which ends the stolen session.

No grace period (ADR-022): a race between two refreshes in the same browser cannot be told
apart from theft, so the frontend ensures only one refresh runs at a time.

Transactions: each public method commits its own work. In the reuse case the family
revocation is committed *before* the error is raised; otherwise the rollback would undo it.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.refresh_tokens import (
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    generate_refresh_token,
    hash_refresh_token,
)
from app.models import RefreshToken, User
from app.repositories.refresh_tokens import RefreshTokenRepository

logger = get_logger("cortex.auth")

USER_AGENT_MAX_LENGTH = 300


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    token: str  # raw value: goes into the cookie, never into storage or logs
    user_id: uuid.UUID
    family_id: uuid.UUID
    expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SessionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        ttl_days: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session = session
        self._tokens = RefreshTokenRepository(session)
        self._ttl = timedelta(days=ttl_days)
        self._clock = clock

    def _new_token(
        self, user_id: uuid.UUID, family_id: uuid.UUID, user_agent: str | None
    ) -> tuple[RefreshToken, IssuedRefreshToken]:
        raw = generate_refresh_token()
        now = self._clock()
        record = RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_refresh_token(raw),
            expires_at=now + self._ttl,
            user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
        )
        self._tokens.add(record)
        issued = IssuedRefreshToken(
            token=raw, user_id=user_id, family_id=family_id, expires_at=record.expires_at
        )
        return record, issued

    async def start(
        self, user_id: uuid.UUID, *, user_agent: str | None = None
    ) -> IssuedRefreshToken:
        """Begin a new session (login): a new family with its first refresh token."""
        _, issued = self._new_token(user_id, uuid.uuid4(), user_agent)
        await self._session.commit()
        return issued

    async def rotate(self, raw_token: str, *, user_agent: str | None = None) -> IssuedRefreshToken:
        """Exchange a valid refresh token for a new one; detect and punish reuse."""
        now = self._clock()
        current = await self._tokens.get_by_hash_for_update(hash_refresh_token(raw_token))
        if current is None:
            await self._session.rollback()
            raise InvalidRefreshTokenError

        if current.revoked_at is not None or current.replaced_by_id is not None:
            revoked = await self._tokens.revoke_family(current.family_id, now=now)
            await self._session.commit()  # the revocation must survive the error below
            logger.warning(
                "auth.refresh_token_reuse_detected",
                user_id=str(current.user_id),
                family_id=str(current.family_id),
                tokens_revoked=revoked,
            )
            raise InvalidRefreshTokenError

        if current.expires_at <= now:
            await self._session.rollback()
            raise RefreshTokenExpiredError

        user = await self._session.get(User, current.user_id)
        if user is None or not user.is_active:
            await self._tokens.revoke_family(current.family_id, now=now)
            await self._session.commit()
            raise InvalidRefreshTokenError

        replacement, issued = self._new_token(
            current.user_id, current.family_id, user_agent or current.user_agent
        )
        await self._session.flush()  # assigns replacement.id
        current.revoked_at = now
        current.replaced_by_id = replacement.id
        await self._session.commit()
        return issued

    async def end(self, raw_token: str) -> None:
        """Log out: revoke the token's whole family. Unknown tokens are ignored (idempotent)."""
        current = await self._tokens.get_by_hash_for_update(hash_refresh_token(raw_token))
        if current is not None:
            await self._tokens.revoke_family(current.family_id, now=self._clock())
        await self._session.commit()

    async def end_all_for_user(
        self, user_id: uuid.UUID, *, keep_family: uuid.UUID | None = None
    ) -> int:
        """Revoke every session of a user (password change, account lock)."""
        revoked = await self._tokens.revoke_all_for_user(
            user_id, now=self._clock(), keep_family=keep_family
        )
        await self._session.commit()
        return revoked

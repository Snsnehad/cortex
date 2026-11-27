"""Data access for refresh tokens. Repositories never commit; the service does."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        """Lock the row until the transaction ends.

        Two concurrent rotations of the same token are serialised here: the second waits for
        the first to commit and then sees the token as already replaced (reuse).
        """
        return await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )

    async def revoke_family(self, family_id: uuid.UUID, *, now: datetime) -> int:
        """Revoke every still-active token of one login session. Returns the count revoked."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, *, now: datetime, keep_family: uuid.UUID | None = None
    ) -> int:
        """Revoke all of a user's sessions (e.g. after a password change), optionally
        keeping the current one."""
        stmt = update(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        if keep_family is not None:
            stmt = stmt.where(RefreshToken.family_id != keep_family)
        result = await self._session.execute(stmt.values(revoked_at=now))
        return int(result.rowcount)  # type: ignore[attr-defined]

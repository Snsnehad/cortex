"""Data access for users. Repositories never commit."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, user: User) -> None:
        self._session.add(user)

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive, matching the uq_users_email_lower index (index-backed lookup)."""
        return await self._session.scalar(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )

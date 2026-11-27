"""Data access for organizations and memberships. Repositories never commit."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Organization, OrganizationMembership


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, organization: Organization) -> None:
        self._session.add(organization)

    def add_membership(self, membership: OrganizationMembership) -> None:
        self._session.add(membership)

    async def slug_exists(self, slug: str) -> bool:
        return bool(await self._session.scalar(select(exists().where(Organization.slug == slug))))

    async def memberships_of(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        """The user's memberships with their organizations loaded, oldest first."""
        rows = await self._session.scalars(
            select(OrganizationMembership)
            .where(OrganizationMembership.user_id == user_id)
            .options(selectinload(OrganizationMembership.organization))
            .order_by(OrganizationMembership.created_at, OrganizationMembership.id)
        )
        return list(rows.all())

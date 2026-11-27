"""Data access for organizations and memberships. Repositories never commit."""

import uuid
from typing import Any

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Organization, OrganizationMembership, OrgRole


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, organization: Organization) -> None:
        self._session.add(organization)

    def add_membership(self, membership: OrganizationMembership) -> None:
        self._session.add(membership)

    async def get(self, organization_id: uuid.UUID) -> Organization | None:
        return await self._session.get(Organization, organization_id)

    async def lock(self, organization_id: uuid.UUID) -> Organization | None:
        """Lock the organization row until the transaction ends.

        Used as a per-organization mutex for membership changes: two concurrent role changes
        or removals in the same organization run one after the other, so the "at least one
        owner" invariant cannot be broken by a race.
        """
        return await self._session.scalar(
            select(Organization)
            .where(Organization.id == organization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_membership(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        """The tenant check behind every org-scoped request: one lookup on the
        (organization_id, user_id) unique index. Both ids are required; querying by user
        alone would let a member of ANY organization through."""
        return await self._session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
            # Always re-read from the database (the session may hold an older copy).
            .execution_options(populate_existing=True)
        )

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == OrgRole.OWNER,
            )
        )
        return int(count or 0)

    async def slug_exists(self, slug: str) -> bool:
        return bool(await self._session.scalar(select(exists().where(Organization.slug == slug))))

    async def memberships_of(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        """The user's memberships with their organizations loaded, oldest first."""
        rows = await self._session.scalars(
            self.memberships_of_query(user_id).order_by(
                OrganizationMembership.created_at, OrganizationMembership.id
            )
        )
        return list(rows.all())

    @staticmethod
    def memberships_of_query(user_id: uuid.UUID) -> Select[Any]:
        """All of a user's memberships, each with its organization (for pagination)."""
        return (
            select(OrganizationMembership)
            .where(OrganizationMembership.user_id == user_id)
            .options(selectinload(OrganizationMembership.organization))
        )

    @staticmethod
    def members_query(organization_id: uuid.UUID) -> Select[Any]:
        """All memberships of one organization, each with its user (for pagination)."""
        return (
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
            .options(selectinload(OrganizationMembership.user))
        )

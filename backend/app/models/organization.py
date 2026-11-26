"""Organizations (tenants) and memberships (DATABASE_DESIGN.md)."""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class OrgRole(StrEnum):
    """Organization-level roles, highest first. Compare with ``OrgRole.rank``."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"

    @property
    def rank(self) -> int:
        return {OrgRole.OWNER: 3, OrgRole.ADMIN: 2, OrgRole.MEMBER: 1}[self]

    def at_least(self, minimum: "OrgRole") -> bool:
        return self.rank >= minimum.rank


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Every tenant-owned row in Cortex carries its ``organization_id``."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    # SET NULL: deleting the creator's account must not delete the organization.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        back_populates="organization", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug!r}>"


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's role in one organization. The source of truth for tenant access checks."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        # One membership per (organization, user). Also the index used by the membership
        # check on every org-scoped request: WHERE organization_id = ? AND user_id = ?
        UniqueConstraint("organization_id", "user_id"),
        # "Which organizations do I belong to?"
        Index(None, "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # varchar + CHECK instead of a native PostgreSQL enum: adding a role later is a simple
    # constraint change rather than an ALTER TYPE migration (DATABASE_DESIGN.md §1).
    role: Mapped[OrgRole] = mapped_column(
        Enum(
            OrgRole,
            name="role",  # → CHECK constraint ck_organization_memberships_role
            native_enum=False,
            create_constraint=True,
            length=16,
            validate_strings=True,
        )
    )

    organization: Mapped[Organization] = relationship(back_populates="memberships", lazy="raise")
    user: Mapped["User"] = relationship(back_populates="memberships", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<OrganizationMembership org={self.organization_id} user={self.user_id} "
            f"role={self.role}>"
        )

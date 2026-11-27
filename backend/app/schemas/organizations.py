"""Request/response models for /api/v1/orgs (API_SPECIFICATION.md)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from app.models import OrganizationMembership, OrgRole
from app.services.organizations import OrganizationWithRole

OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class OrganizationCreate(BaseModel):
    name: OrgName


class OrganizationUpdate(BaseModel):
    name: OrgName


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    role: OrgRole  # the caller's role in this organization

    @classmethod
    def from_domain(cls, item: OrganizationWithRole) -> "OrganizationOut":
        org = item.organization
        return cls(
            id=org.id, name=org.name, slug=org.slug, created_at=org.created_at, role=item.role
        )

    @classmethod
    def from_membership(cls, membership: OrganizationMembership) -> "OrganizationOut":
        return cls.from_domain(OrganizationWithRole(membership.organization, membership.role))


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class ChangeRoleRequest(BaseModel):
    role: OrgRole


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: OrgRole
    joined_at: datetime

    @classmethod
    def from_membership(cls, membership: OrganizationMembership) -> "MemberOut":
        return cls(
            user_id=membership.user_id,
            email=membership.user.email,
            full_name=membership.user.full_name,
            role=membership.role,
            joined_at=membership.created_at,
        )

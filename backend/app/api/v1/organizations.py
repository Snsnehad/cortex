"""/api/v1/orgs: organizations and their members.

Every ``/orgs/{org_id}/...`` route takes an org context dependency, so membership is verified
(404 for non-members) before the handler runs. Fine-grained membership rules (owners only for
OWNER changes, last-owner protection) live in ``OrganizationService``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.dependencies.auth import CurrentUser
from app.dependencies.database import DbSession
from app.dependencies.orgs import AdminContext, OrgContextDep, OwnerContext
from app.dependencies.pagination import CursorCodecDep, PageParams
from app.schemas.organizations import (
    AddMemberRequest,
    ChangeRoleRequest,
    MemberOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)
from app.schemas.pagination import Page
from app.services.organizations import OrganizationService

router = APIRouter(prefix="/orgs", tags=["organizations"])


def get_organization_service(session: DbSession) -> OrganizationService:
    return OrganizationService(session)


Organizations = Annotated[OrganizationService, Depends(get_organization_service)]


@router.get("", response_model=Page[OrganizationOut])
async def list_my_organizations(
    user: CurrentUser, page: PageParams, codec: CursorCodecDep, service: Organizations
) -> Page[OrganizationOut]:
    result = await service.list_mine(user, page=page, codec=codec)
    return Page[OrganizationOut].from_result(result, OrganizationOut.from_membership)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizationOut)
async def create_organization(
    body: OrganizationCreate, user: CurrentUser, service: Organizations
) -> OrganizationOut:
    return OrganizationOut.from_domain(await service.create(body.name, owner=user))


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(ctx: OrgContextDep, service: Organizations) -> OrganizationOut:
    return OrganizationOut.from_domain(await service.get(ctx))


@router.patch("/{org_id}", response_model=OrganizationOut)
async def rename_organization(
    body: OrganizationUpdate, ctx: OwnerContext, service: Organizations
) -> OrganizationOut:
    return OrganizationOut.from_domain(await service.rename(ctx, name=body.name))


@router.get("/{org_id}/members", response_model=Page[MemberOut])
async def list_members(
    ctx: OrgContextDep, page: PageParams, codec: CursorCodecDep, service: Organizations
) -> Page[MemberOut]:
    result = await service.list_members(ctx, page=page, codec=codec)
    return Page[MemberOut].from_result(result, MemberOut.from_membership)


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED, response_model=MemberOut)
async def add_member(
    body: AddMemberRequest, ctx: AdminContext, service: Organizations
) -> MemberOut:
    membership = await service.add_member(ctx, email=str(body.email), role=body.role)
    return MemberOut.from_membership(membership)


@router.patch("/{org_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    user_id: uuid.UUID, body: ChangeRoleRequest, ctx: AdminContext, service: Organizations
) -> MemberOut:
    membership = await service.change_role(ctx, user_id=user_id, role=body.role)
    await service.load_user(membership)
    return MemberOut.from_membership(membership)


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: OrgContextDep, service: Organizations) -> None:
    # Any member may call this (leaving is always allowed); the service checks the rest.
    await service.remove_member(ctx, user_id=user_id)

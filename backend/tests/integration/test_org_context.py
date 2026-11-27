"""Organization context and role checks: the tenant boundary (TASK-03.05).

Test routes are mounted on the real app (/api/v1/orgs/{org_id}/_test/...) so the full
dependency chain runs: bearer token → active user → membership lookup → role check.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.dependencies.orgs import AdminContext, OrgContextDep, OwnerContext
from app.models import Organization, OrganizationMembership, OrgRole, User
from tests.integration.factories import add_member, create_org, create_user

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
route_logger = get_logger("cortex.test.orgs")


@pytest.fixture(autouse=True)
def org_routes(api_app: FastAPI) -> None:
    def body(ctx: Any) -> dict[str, str]:
        return {"organization_id": str(ctx.organization_id), "role": ctx.role.value}

    @api_app.get("/api/v1/orgs/{org_id}/_test/member")
    async def member_route(ctx: OrgContextDep) -> dict[str, str]:
        route_logger.info("test.inside_route")
        return body(ctx)

    @api_app.get("/api/v1/orgs/{org_id}/_test/admin")
    async def admin_route(ctx: AdminContext) -> dict[str, str]:
        return body(ctx)

    @api_app.get("/api/v1/orgs/{org_id}/_test/owner")
    async def owner_route(ctx: OwnerContext) -> dict[str, str]:
        return body(ctx)


def bearer(api_app: FastAPI, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_app.state.access_tokens.issue(user.id).token}"}


async def get(
    client: httpx.AsyncClient, org_id: object, level: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    return await client.get(f"/api/v1/orgs/{org_id}/_test/{level}", headers=headers or {})


async def org_with(factory: Factory, role: OrgRole) -> tuple[Organization, User]:
    owner = await create_user(factory)
    org = await create_org(factory, owner=owner)
    if role is OrgRole.OWNER:
        return org, owner
    user = await create_user(factory)
    await add_member(factory, org, user, role)
    return org, user


# --- role matrix ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "level", "status"),
    [
        (OrgRole.MEMBER, "member", 200),
        (OrgRole.MEMBER, "admin", 403),
        (OrgRole.MEMBER, "owner", 403),
        (OrgRole.ADMIN, "member", 200),
        (OrgRole.ADMIN, "admin", 200),
        (OrgRole.ADMIN, "owner", 403),
        (OrgRole.OWNER, "member", 200),
        (OrgRole.OWNER, "admin", 200),
        (OrgRole.OWNER, "owner", 200),
    ],
)
async def test_role_matrix(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    role: OrgRole,
    level: str,
    status: int,
) -> None:
    org, user = await org_with(session_factory, role)

    response = await get(api_client, org.id, level, bearer(api_app, user))

    assert response.status_code == status
    if status == 200:
        assert response.json() == {"organization_id": str(org.id), "role": role.value}


async def test_insufficient_role_explains_what_is_needed(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    org, member = await org_with(session_factory, OrgRole.MEMBER)

    error = (await get(api_client, org.id, "admin", bearer(api_app, member))).json()["error"]

    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"] == {"required_role": "ADMIN"}


# --- tenant isolation -----------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["member", "admin", "owner"])
async def test_member_of_another_org_gets_404_everywhere(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory, level: str
) -> None:
    org_a, _ = await org_with(session_factory, OrgRole.OWNER)
    _, owner_of_b = await org_with(session_factory, OrgRole.OWNER)  # powerful, but elsewhere

    response = await get(api_client, org_a.id, level, bearer(api_app, owner_of_b))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"


async def test_unknown_org_and_foreign_org_are_indistinguishable(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    foreign_org, _ = await org_with(session_factory, OrgRole.OWNER)
    _, outsider = await org_with(session_factory, OrgRole.OWNER)
    headers = bearer(api_app, outsider)

    foreign = await get(api_client, foreign_org.id, "member", headers)
    unknown = await get(api_client, uuid.uuid4(), "member", headers)

    def without_request_id(response: httpx.Response) -> dict[str, Any]:
        error = dict(response.json()["error"])
        error.pop("request_id")
        return error

    assert (foreign.status_code, unknown.status_code) == (404, 404)
    assert without_request_id(foreign) == without_request_id(unknown)


async def test_malformed_org_id_is_422(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    _, user = await org_with(session_factory, OrgRole.OWNER)

    response = await get(api_client, "not-a-uuid", "member", bearer(api_app, user))

    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["loc"] == ["path", "org_id"]


# --- authentication comes first -------------------------------------------------------------------


async def test_anonymous_callers_learn_nothing_about_organizations(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    org, _ = await org_with(session_factory, OrgRole.OWNER)

    existing = await get(api_client, org.id, "member")
    unknown = await get(api_client, uuid.uuid4(), "member")

    assert (existing.status_code, unknown.status_code) == (401, 401)
    assert existing.json()["error"]["code"] == unknown.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_deactivated_member_is_rejected(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    org, user = await org_with(session_factory, OrgRole.ADMIN)
    headers = bearer(api_app, user)
    async with session_factory() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()

    assert (await get(api_client, org.id, "member", headers)).status_code == 401


# --- changes apply immediately --------------------------------------------------------------------


async def test_role_changes_and_removal_apply_to_the_next_request(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    org, user = await org_with(session_factory, OrgRole.MEMBER)
    headers = bearer(api_app, user)  # the same token throughout: roles are not in it
    assert (await get(api_client, org.id, "admin", headers)).status_code == 403

    async with session_factory() as session:
        await session.execute(
            update(OrganizationMembership)
            .where(OrganizationMembership.user_id == user.id)
            .values(role=OrgRole.ADMIN)
        )
        await session.commit()
    assert (await get(api_client, org.id, "admin", headers)).status_code == 200

    async with session_factory() as session:
        await session.execute(
            delete(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        )
        await session.commit()
    assert (await get(api_client, org.id, "member", headers)).status_code == 404


# --- request-scoped log context -------------------------------------------------------------------


async def test_logs_carry_user_and_org_ids_without_leaking_between_requests(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    org, user = await org_with(session_factory, OrgRole.MEMBER)
    capsys.readouterr()

    await get(api_client, org.id, "member", bearer(api_app, user))
    await api_client.get("/api/v1/health/live")  # anonymous request right after

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]
    [inside] = [line for line in lines if line["event"] == "test.inside_route"]
    access = [line for line in lines if line["event"] == "http.request"]
    assert (inside["user_id"], inside["organization_id"]) == (str(user.id), str(org.id))
    assert (access[0]["user_id"], access[0]["organization_id"]) == (str(user.id), str(org.id))
    # The anonymous request's access log must not inherit the previous caller's identity.
    anonymous = [line for line in access if line["path"] == "/api/v1/health/live"]
    assert all("user_id" not in line and "organization_id" not in line for line in anonymous)
    assert not {"user_id", "organization_id"} & set(structlog.contextvars.get_contextvars())

"""Tenant isolation, route by route (TASK-03.08, ADR-005).

For every tenant-scoped route in ``tests/integration/tenancy.py``:

1. **Foreign organization**: each user of organization B (owner, admin, member) calls the
   route with organization A's id. The answer must be 404 and *identical* to the answer for
   an organization id that does not exist, and the database must not change at all.
2. **Foreign resource through one's own organization**: B's owner calls the route with B's id
   but A's resource ids (e.g. ``PATCH /orgs/{B}/members/{a member of A}``). Again: identical
   to an unknown resource id, and nothing changes.
3. **Positive control**: A's owner makes the same request and succeeds, which proves the
   probes reached a real, working route (a mistyped path would also answer 404).

``test_every_route_is_classified`` keeps the registry complete.
"""

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from tests.integration.tenancy import (
    ISOLATION_CASES,
    NOT_TENANT_SCOPED,
    ORG_PREFIX,
    IsolationCase,
    World,
    build_world,
    fill,
    snapshot,
)

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
ATTACKERS = ("owner_b", "admin_b", "member_b")
cases = pytest.mark.parametrize("case", ISOLATION_CASES, ids=lambda c: c.id)


@pytest.fixture
async def world(session_factory: Factory) -> World:
    return await build_world(session_factory)


async def send(
    app: FastAPI,
    client: httpx.AsyncClient,
    case: IsolationCase,
    world: World,
    user: User,
    path: str,
) -> httpx.Response:
    token = app.state.access_tokens.issue(user.id).token
    return await client.request(
        case.method,
        path,
        json=case.body(world) if case.body else None,
        files=case.files(world) if case.files else None,
        headers={"Authorization": f"Bearer {token}"},
    )


def comparable(response: httpx.Response) -> tuple[int, dict[str, Any]]:
    """Status and error body without the per-request id."""
    error = dict(response.json()["error"])
    error.pop("request_id", None)
    return response.status_code, error


# --- the registry is complete ---------------------------------------------------------------


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
APP_DIR = Path(__file__).resolve().parents[2] / "app"


def api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every API operation, from the OpenAPI schema.

    The schema is FastAPI's public list of routes (since 0.165 ``app.routes`` holds lazy,
    private ``_IncludedRouter`` nodes instead of flat routes). Routes hidden from the schema
    are forbidden by ``test_no_route_is_hidden_from_the_schema``, so none can escape.
    """
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in HTTP_METHODS
    }


def test_no_route_is_hidden_from_the_schema() -> None:
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if "include_in_schema=False" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"hidden routes would escape the isolation registry: {offenders}"


async def test_every_route_is_classified(api_app: FastAPI) -> None:
    routes = api_routes(api_app)
    assert routes, "no routes found: the route discovery is broken"
    registered = [(case.method, case.path) for case in ISOLATION_CASES]

    assert len(registered) == len(set(registered)), "duplicate isolation cases"
    assert not set(registered) & set(NOT_TENANT_SCOPED), "route classified twice"

    unclassified = routes - set(registered) - set(NOT_TENANT_SCOPED)
    assert not unclassified, (
        f"routes without a tenant-isolation classification: {sorted(unclassified)}. "
        "Add an IsolationCase (or a NOT_TENANT_SCOPED reason) in tests/integration/tenancy.py."
    )
    stale = (set(registered) | set(NOT_TENANT_SCOPED)) - routes
    assert not stale, f"registry entries for routes that no longer exist: {sorted(stale)}"


def test_org_scoped_routes_are_never_excused() -> None:
    """A route under /orgs/{org_id} must be probed, never waved through with a reason."""
    excused = [path for _, path in NOT_TENANT_SCOPED if path.startswith(ORG_PREFIX)]
    assert not excused
    assert all(case.path.startswith(ORG_PREFIX) for case in ISOLATION_CASES)


# --- probes ---------------------------------------------------------------------------------


@cases
async def test_foreign_organization_is_indistinguishable_from_none(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    world: World,
    case: IsolationCase,
) -> None:
    before = await snapshot(session_factory)
    org_a_path = fill(case.path, world.org_a.id, world.resources_a)
    unknown_path = fill(case.path, uuid.uuid4(), world.resources_a)

    for attacker in ATTACKERS:
        user = world.users[attacker]
        foreign = await send(api_app, api_client, case, world, user, org_a_path)
        unknown = await send(api_app, api_client, case, world, user, unknown_path)

        assert foreign.status_code == 404, (attacker, foreign.text)
        assert comparable(foreign) == comparable(unknown), attacker

    assert await snapshot(session_factory) == before


@pytest.mark.parametrize(
    "case", [c for c in ISOLATION_CASES if c.resource_params], ids=lambda c: c.id
)
async def test_foreign_resource_through_own_organization_is_not_found(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    world: World,
    case: IsolationCase,
) -> None:
    before = await snapshot(session_factory)
    owner_b = world.users["owner_b"]
    unknown_resources = {name: str(uuid.uuid4()) for name in case.resource_params}

    foreign = await send(
        api_app,
        api_client,
        case,
        world,
        owner_b,
        fill(case.path, world.org_b.id, world.resources_a),
    )
    unknown = await send(
        api_app,
        api_client,
        case,
        world,
        owner_b,
        fill(case.path, world.org_b.id, {**world.resources_a, **unknown_resources}),
    )

    assert foreign.status_code == 404, foreign.text
    assert comparable(foreign) == comparable(unknown)
    assert await snapshot(session_factory) == before


@cases
async def test_positive_control_the_owner_of_a_succeeds(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    world: World,
    case: IsolationCase,
) -> None:
    path = fill(case.path, world.org_a.id, world.resources_a)

    response = await send(api_app, api_client, case, world, world.users["owner_a"], path)

    assert response.status_code == case.ok_status, response.text

"""Tenant-isolation registry: every API route, classified (TASK-03.08).

Every route of the application must appear here exactly once, either as

* an ``IsolationCase`` (tenant-scoped: it lives under ``/orgs/{org_id}``), which
  ``test_tenant_isolation.py`` probes from another organization, or
* an entry in ``NOT_TENANT_SCOPED`` with the reason it needs no probe.

``test_every_route_is_classified`` fails when a route is added without one, so a new
endpoint cannot ship without a cross-tenant test.

Adding a tenant-scoped route
----------------------------
Add an ``IsolationCase`` with the route's method and path exactly as FastAPI declares it
(``/api/v1/orgs/{org_id}/...``). Path parameters other than ``org_id`` are filled from
``World.resources_a`` (things that belong to organization A): add the resource to
``build_world`` when a new kind appears (knowledge bases, documents, conversations ...).
``body`` builds a *valid* request body, so the probe reaches the tenant check rather than
failing validation, and the positive control (organization A's owner) succeeds.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import Base
from app.models import Organization, OrgRole, User
from app.storage import StorageBackend
from tests.integration.db_helpers import add_all_and_commit, new_document
from tests.integration.factories import add_member, create_kb, create_org, create_user

Factory = async_sessionmaker[AsyncSession]
TENANT_A_FILE = b"Tenant A confidential handbook"
ORG_PREFIX = "/api/v1/orgs/{org_id}"


@dataclass(frozen=True)
class World:
    """Two organizations with a full set of roles each, plus a user in neither."""

    org_a: Organization
    org_b: Organization
    users: dict[str, User]  # owner_a, admin_a, member_a, owner_b, admin_b, member_b, outsider
    # Path-parameter values that point at organization A's resources.
    resources_a: dict[str, str]


async def build_world(factory: Factory, storage: StorageBackend) -> World:
    users: dict[str, User] = {}
    orgs: dict[str, Organization] = {}
    for tenant in ("a", "b"):
        owner = await create_user(factory, full_name=f"Owner {tenant.upper()}")
        orgs[tenant] = await create_org(factory, owner=owner, name=f"Tenant {tenant.upper()}")
        users[f"owner_{tenant}"] = owner
        for role in (OrgRole.ADMIN, OrgRole.MEMBER):
            user = await create_user(factory)
            await add_member(factory, orgs[tenant], user, role)
            users[f"{role.value.lower()}_{tenant}"] = user
    users["outsider"] = await create_user(factory)
    kb_a = await create_kb(factory, orgs["a"], name="Tenant A handbook")
    # A document of A whose file really exists, so the download positive control can work.
    doc_a = new_document(kb_a, content=TENANT_A_FILE.decode())
    await add_all_and_commit(factory, doc_a)
    await storage.put(doc_a.storage_key, TENANT_A_FILE, content_type=doc_a.mime_type)
    return World(
        org_a=orgs["a"],
        org_b=orgs["b"],
        users=users,
        resources_a={
            "user_id": str(users["member_a"].id),
            "kb_id": str(kb_a.id),
            "document_id": str(doc_a.id),
        },
    )


@dataclass(frozen=True)
class IsolationCase:
    method: str
    path: str  # as declared by FastAPI, e.g. /api/v1/orgs/{org_id}/members/{user_id}
    body: Callable[[World], dict[str, Any]] | None = None
    # Multipart file fields instead of a JSON body: {"file": (name, bytes, content type)}.
    files: Callable[[World], dict[str, Any]] | None = None
    # Status organization A's owner gets with the same request (the positive control).
    ok_status: int = 200
    resource_params: Sequence[str] = field(default=())

    @property
    def id(self) -> str:
        return f"{self.method} {self.path.removeprefix(ORG_PREFIX) or '/'}"


ISOLATION_CASES: list[IsolationCase] = [
    IsolationCase("GET", ORG_PREFIX),
    IsolationCase("PATCH", ORG_PREFIX, body=lambda w: {"name": "Renamed by intruder"}),
    IsolationCase("GET", f"{ORG_PREFIX}/members"),
    IsolationCase(
        "POST",
        f"{ORG_PREFIX}/members",
        body=lambda w: {"email": w.users["outsider"].email, "role": "MEMBER"},
        ok_status=201,
    ),
    IsolationCase(
        "PATCH",
        f"{ORG_PREFIX}/members/{{user_id}}",
        body=lambda w: {"role": "OWNER"},
        resource_params=("user_id",),
    ),
    IsolationCase(
        "DELETE",
        f"{ORG_PREFIX}/members/{{user_id}}",
        ok_status=204,
        resource_params=("user_id",),
    ),
    # --- knowledge bases (TASK-04.02) ---
    IsolationCase(
        "POST",
        f"{ORG_PREFIX}/knowledge-bases",
        body=lambda w: {"name": "Created by intruder"},
        ok_status=201,
    ),
    IsolationCase("GET", f"{ORG_PREFIX}/knowledge-bases"),
    IsolationCase("GET", f"{ORG_PREFIX}/knowledge-bases/{{kb_id}}", resource_params=("kb_id",)),
    IsolationCase(
        "PATCH",
        f"{ORG_PREFIX}/knowledge-bases/{{kb_id}}",
        body=lambda w: {"name": "Renamed by intruder", "chunk_size_tokens": 256},
        resource_params=("kb_id",),
    ),
    IsolationCase(
        "DELETE",
        f"{ORG_PREFIX}/knowledge-bases/{{kb_id}}",
        ok_status=202,
        resource_params=("kb_id",),
    ),
    # --- documents (TASK-04.05) ---
    IsolationCase(
        "POST",
        f"{ORG_PREFIX}/knowledge-bases/{{kb_id}}/documents",
        files=lambda w: {"file": ("intruder.txt", b"planted by another tenant", "text/plain")},
        ok_status=202,
        resource_params=("kb_id",),
    ),
    # --- document reads (TASK-04.06) ---
    IsolationCase(
        "GET", f"{ORG_PREFIX}/knowledge-bases/{{kb_id}}/documents", resource_params=("kb_id",)
    ),
    IsolationCase(
        "GET", f"{ORG_PREFIX}/documents/{{document_id}}", resource_params=("document_id",)
    ),
    IsolationCase(
        "GET", f"{ORG_PREFIX}/documents/{{document_id}}/status", resource_params=("document_id",)
    ),
    IsolationCase(
        "GET", f"{ORG_PREFIX}/documents/{{document_id}}/download", resource_params=("document_id",)
    ),
    # --- reprocess and delete (TASK-05.05) ---
    IsolationCase(
        "POST",
        f"{ORG_PREFIX}/documents/{{document_id}}/reprocess",
        ok_status=202,
        resource_params=("document_id",),
    ),
    IsolationCase(
        "DELETE",
        f"{ORG_PREFIX}/documents/{{document_id}}",
        ok_status=202,
        resource_params=("document_id",),
    ),
]

# Routes with no organization in the path, and why that is safe.
NOT_TENANT_SCOPED: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/health/live"): "public probe, no data",
    ("GET", "/api/v1/health/ready"): "public probe, dependency status only",
    ("POST", "/api/v1/auth/register"): "creates a new account and organization",
    ("POST", "/api/v1/auth/login"): "credentials only",
    ("POST", "/api/v1/auth/refresh"): "acts on the caller's own refresh token",
    ("POST", "/api/v1/auth/logout"): "acts on the caller's own refresh token",
    ("GET", "/api/v1/auth/me"): "the caller's own account and memberships",
    ("PATCH", "/api/v1/auth/me"): "the caller's own account",
    ("POST", "/api/v1/auth/me/password"): "the caller's own account",
    ("GET", "/api/v1/orgs"): "lists only the caller's own memberships",
    ("POST", "/api/v1/orgs"): "creates a new organization owned by the caller",
}


def fill(path: str, org_id: object, resources: dict[str, str]) -> str:
    return path.format(org_id=org_id, **resources)


async def snapshot(factory: Factory) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, to prove a refused request changed nothing anywhere."""
    state: dict[str, list[tuple[Any, ...]]] = {}
    async with factory() as session:
        for table in Base.metadata.sorted_tables:
            rows = (await session.execute(select(table))).all()
            state[table.name] = sorted((tuple(row) for row in rows), key=repr)
    return state

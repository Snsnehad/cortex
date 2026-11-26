"""Factories that persist rows with sensible, unique defaults.

Use these when a test needs data but does not care about its exact values::

    owner = await create_user(session_factory)
    org = await create_org(session_factory, owner=owner)          # owner gets OWNER role
    member = await create_user(session_factory)
    await add_member(session_factory, org, member, OrgRole.ADMIN)
    kb = await create_kb(session_factory, org)
    doc = await create_document(session_factory, kb)

For constraint tests that need full control (e.g. deliberately invalid rows), use the unsaved
builders in ``tests/integration/db_helpers.py`` instead.
"""

import itertools

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    Organization,
    OrganizationMembership,
    OrgRole,
    User,
)
from tests.integration.db_helpers import add_all_and_commit, new_document, new_kb

Factory = async_sessionmaker[AsyncSession]
_sequence = itertools.count(1)


def _next() -> int:
    return next(_sequence)


async def create_user(
    factory: Factory,
    *,
    email: str | None = None,
    full_name: str | None = None,
    is_active: bool = True,
) -> User:
    n = _next()
    user = User(
        email=email or f"user{n}@example.com",
        password_hash="argon2-hash-placeholder",
        full_name=full_name or f"Test User {n}",
        is_active=is_active,
    )
    await add_all_and_commit(factory, user)
    return user


async def add_member(
    factory: Factory, org: Organization, user: User, role: OrgRole = OrgRole.MEMBER
) -> OrganizationMembership:
    membership = OrganizationMembership(organization_id=org.id, user_id=user.id, role=role)
    await add_all_and_commit(factory, membership)
    return membership


async def create_org(
    factory: Factory, *, owner: User | None = None, name: str | None = None
) -> Organization:
    n = _next()
    org = Organization(
        name=name or f"Org {n}",
        slug=f"org-{n}",
        created_by_user_id=owner.id if owner else None,
    )
    await add_all_and_commit(factory, org)
    if owner is not None:
        await add_member(factory, org, owner, OrgRole.OWNER)
    return org


async def create_kb(
    factory: Factory, org: Organization, *, name: str | None = None
) -> KnowledgeBase:
    kb = new_kb(org, name=name or f"Knowledge Base {_next()}")
    await add_all_and_commit(factory, kb)
    return kb


async def create_document(
    factory: Factory,
    kb: KnowledgeBase,
    *,
    status: DocumentStatus = DocumentStatus.PENDING,
) -> Document:
    document = new_document(kb, content=f"document-{_next()}", status=status)
    await add_all_and_commit(factory, document)
    return document

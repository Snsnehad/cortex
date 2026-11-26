"""The test harness itself (TASK-02.08).

These guard the guarantees every other integration test relies on: isolation between tests,
the API client really using the scratch database, and factories producing valid, unique rows.
"""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.base import Base
from app.dependencies.database import DbSession
from app.models import DocumentStatus, OrganizationMembership, OrgRole
from tests.integration.conftest import clear_all_tables
from tests.integration.factories import (
    add_member,
    create_document,
    create_kb,
    create_org,
    create_user,
)

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


async def row_counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            table.name: int(await conn.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


async def test_every_test_starts_with_empty_tables(db_engine: AsyncEngine) -> None:
    # With random ordering, a test that leaked rows would eventually run right before this
    # one and make it fail.
    assert set((await row_counts(db_engine)).values()) == {0}


async def test_clear_all_tables_handles_full_foreign_key_chains(
    db_engine: AsyncEngine, session_factory: Factory
) -> None:
    owner = await create_user(session_factory)
    org = await create_org(session_factory, owner=owner)
    kb = await create_kb(session_factory, org)
    await create_document(session_factory, kb)

    await clear_all_tables(db_engine)

    assert set((await row_counts(db_engine)).values()) == {0}


async def test_api_client_uses_the_scratch_database(
    api_app: FastAPI, api_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    @api_app.get("/_test/current-database")
    async def current_database(session: DbSession) -> dict[str, str]:
        return {"name": str(await session.scalar(text("SELECT current_database()")))}

    response = await api_client.get("/_test/current-database")

    assert response.json()["name"] == test_settings.database.name
    assert response.json()["name"].startswith("cortex_modeltest_")


async def test_api_client_sees_rows_written_by_factories(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    owner = await create_user(session_factory)
    await create_org(session_factory, owner=owner)

    @api_app.get("/_test/membership-count")
    async def membership_count(session: DbSession) -> dict[str, int]:
        count = await session.scalar(select(func.count()).select_from(OrganizationMembership))
        return {"count": int(count or 0)}

    assert (await api_client.get("/_test/membership-count")).json() == {"count": 1}


async def test_factories_produce_unique_valid_rows(session_factory: Factory) -> None:
    users = [await create_user(session_factory) for _ in range(3)]
    org = await create_org(session_factory, owner=users[0])
    await add_member(session_factory, org, users[1], OrgRole.ADMIN)
    kbs = [await create_kb(session_factory, org) for _ in range(2)]
    docs = [await create_document(session_factory, kbs[0]) for _ in range(3)]

    assert len({u.email for u in users}) == 3
    assert len({kb.name for kb in kbs}) == 2
    assert len({d.content_hash for d in docs}) == 3
    assert all(d.status is DocumentStatus.PENDING for d in docs)
    async with session_factory() as session:
        roles = (
            await session.scalars(
                select(OrganizationMembership.role).order_by(OrganizationMembership.role)
            )
        ).all()
    assert sorted(roles) == sorted([OrgRole.OWNER, OrgRole.ADMIN])

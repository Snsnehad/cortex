"""Engine, session-per-request and mixins against real PostgreSQL (TASK-02.01).

Uses a throwaway table on an isolated MetaData; it is created before and dropped after each
test. (A dedicated test database arrives with the test harness in TASK-02.08.)
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import MetaData, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import Settings
from app.config.settings import DatabaseSettings
from app.database.base import NAMING_CONVENTION, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.database.session import create_engine
from app.dependencies.database import DbSession
from app.main import create_app

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


class IsolatedBase(Base):
    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Item(UUIDPrimaryKeyMixin, TimestampMixin, IsolatedBase):
    __tablename__ = "zz_test_task_0201_items"

    name: Mapped[str] = mapped_column(String(100))


def build_app(settings: Settings, seen_sessions: list[AsyncSession]) -> FastAPI:
    app = create_app(settings)

    @app.post("/_test/items")
    async def create_item(session: DbSession, name: str, fail: bool = False) -> dict[str, str]:
        seen_sessions.append(session)
        item = Item(name=name)
        session.add(item)
        await session.flush()  # the INSERT has run inside the open transaction
        if fail:
            raise RuntimeError("simulated failure after a write")
        await session.commit()
        return {
            "id": str(item.id),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    return app


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = build_app(settings, seen_sessions=[])
    engine: AsyncEngine = app.state.db_engine
    async with engine.begin() as conn:
        await conn.run_sync(IsolatedBase.metadata.drop_all)
        await conn.run_sync(IsolatedBase.metadata.create_all)
    yield app
    async with engine.begin() as conn:
        await conn.run_sync(IsolatedBase.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def count_items(app: FastAPI, name: str) -> int:
    async with app.state.session_factory() as session:
        result = await session.scalar(
            select(func.count()).select_from(Item).where(Item.name == name)
        )
        return int(result or 0)


def checked_out(app: FastAPI) -> int:
    return int(app.state.db_engine.pool.checkedout())


# --- mixins against PostgreSQL -------------------------------------------------------------


async def test_insert_sets_uuid7_and_server_timestamps(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.post("/_test/items", params={"name": "first"})

    assert response.status_code == 200
    body = response.json()
    created_at = datetime.fromisoformat(body["created_at"])
    assert created_at.tzinfo is not None
    assert abs(datetime.now(UTC) - created_at) < timedelta(minutes=1)
    assert body["created_at"] == body["updated_at"]
    assert body["id"][14] == "7"  # version nibble of a UUIDv7


async def test_update_bumps_updated_at_and_is_readable_without_lazy_load(app: FastAPI) -> None:
    async with app.state.session_factory() as session:
        item = Item(name="to-update")
        session.add(item)
        await session.commit()
        created_at, first_updated_at = item.created_at, item.updated_at

    await asyncio.sleep(0.01)  # now() is per transaction; make the second one later

    async with app.state.session_factory() as session:
        item = await session.get_one(Item, item.id)
        item.name = "updated"
        await session.commit()
        # Accessing updated_at right after an UPDATE would raise MissingGreenlet without
        # eager_defaults (the attribute would be expired and lazily reloaded).
        assert item.updated_at > first_updated_at
        assert item.created_at == created_at


# --- session per request --------------------------------------------------------------------


async def test_failed_request_rolls_back_and_releases_its_connection(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.post("/_test/items", params={"name": "doomed", "fail": True})

    assert response.status_code == 500
    assert await count_items(app, "doomed") == 0
    assert checked_out(app) == 0


async def test_each_request_gets_its_own_session(settings: Settings) -> None:
    seen: list[AsyncSession] = []
    app = build_app(settings, seen)
    async with app.state.db_engine.begin() as conn:
        await conn.run_sync(IsolatedBase.metadata.create_all)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/_test/items", params={"name": "a"})
            await client.post("/_test/items", params={"name": "b"})
        assert len(seen) == 2
        assert seen[0] is not seen[1]
        assert all(not session.in_transaction() for session in seen)
    finally:
        async with app.state.db_engine.begin() as conn:
            await conn.run_sync(IsolatedBase.metadata.drop_all)
        await app.state.db_engine.dispose()


async def test_no_connection_leak_after_100_requests(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    for i in range(100):
        response = await client.post(
            "/_test/items", params={"name": f"leak-{i}", "fail": i % 10 == 0}
        )
        assert response.status_code == (500 if i % 10 == 0 else 200)

    pool = app.state.db_engine.pool
    assert pool.checkedout() == 0
    assert pool.size() <= settings.database.pool_size
    assert await count_items(app, "leak-0") == 0  # failed requests left nothing behind
    assert await count_items(app, "leak-1") == 1


async def test_concurrent_requests_stay_within_pool_limits(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    responses = await asyncio.gather(
        *(client.post("/_test/items", params={"name": f"burst-{i}"}) for i in range(40))
    )

    assert all(r.status_code == 200 for r in responses)
    pool = app.state.db_engine.pool
    assert pool.checkedout() == 0
    assert pool.overflow() <= settings.database.max_overflow


# --- engine configuration and lifecycle -----------------------------------------------------


async def test_engine_identifies_itself_to_postgres(settings: Settings) -> None:
    engine = create_engine(settings, application_name="cortex-test")
    try:
        async with engine.connect() as conn:
            name = await conn.scalar(text("SHOW application_name"))
        assert name == "cortex-test"
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        await engine.dispose()


async def test_engine_url_hides_the_password() -> None:
    # A distinctive password: the dev default "cortex" is also the user and database name.
    settings = Settings(
        _env_file=None, database=DatabaseSettings(password=SecretStr("Sup3r-S3cret-Pw!"))
    )
    engine = create_engine(settings)  # no connection is opened
    try:
        assert "Sup3r-S3cret-Pw!" not in str(engine.url)
        assert "Sup3r-S3cret-Pw!" not in repr(engine)
        assert engine.url.password == "Sup3r-S3cret-Pw!"  # still usable for connecting
    finally:
        await engine.dispose()


async def test_lifespan_shutdown_disposes_the_pool(app: FastAPI) -> None:
    engine: AsyncEngine = app.state.db_engine
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    pool_before = engine.pool

    async with app.router.lifespan_context(app):
        pass

    assert engine.pool is not pool_before  # dispose() replaced the pool
    assert isinstance(pool_before, AsyncAdaptedQueuePool)
    assert pool_before.checkedin() == 0  # the old pool holds no idle connections

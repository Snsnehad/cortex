"""Shared fixtures for integration tests that need their own PostgreSQL database.

* ``database_url``: an empty scratch database per test (migration tests).
* ``migrated_database_url``: one scratch database per test session, built by running the
  real Alembic migrations, so model tests exercise exactly the schema production gets
  (not ``metadata.create_all()``, which could hide a broken migration).
* ``db_engine`` / ``session_factory``: async access to the migrated database; every table is
  emptied after each test, so tests never see each other's rows.
* ``test_settings`` / ``api_app`` / ``api_client``: the real application, wired to the
  migrated scratch database, driven in-process over httpx.

Scratch databases are dropped WITH (FORCE) afterwards; the development database is never
touched. The database-creating fixtures are synchronous because Alembic commands call
``asyncio.run``. Full guide: docs/testing/README.md.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  (every table on Base.metadata)
from app.config import Settings
from app.database.base import Base
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


@contextmanager
def scratch_database(prefix: str) -> Iterator[str]:
    """Create a uniquely named database; yield its SQLAlchemy URL; drop it afterwards."""
    db = Settings().database
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin_conninfo = psycopg.conninfo.make_conninfo(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password.get_secret_value(),
        dbname="postgres",
    )
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield db.model_copy(update={"name": name}).url
    finally:
        with psycopg.connect(admin_conninfo, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def make_alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = database_url
    config.attributes["skip_logging_setup"] = True
    return config


@pytest.fixture
def database_url() -> Iterator[str]:
    with scratch_database("cortex_migtest") as url:
        yield url


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    with scratch_database("cortex_modeltest") as url:
        command.upgrade(make_alembic_config(url), "head")
        yield url


@pytest.fixture
async def db_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    # A normal connection pool, scoped to ONE test: connections are reused across the
    # several sessions a test opens (a new connection costs ~33 ms), and the pool is disposed
    # before the test's event loop closes, so no connection outlives its loop.
    engine = create_async_engine(migrated_database_url, pool_size=5, max_overflow=5)
    yield engine
    await clear_all_tables(engine)
    await engine.dispose()


_CLEAR_ALL_SQL = "; ".join(
    # Table names come from our own model metadata, never from input.
    f'DELETE FROM "{table.name}"'  # noqa: S608
    for table in reversed(Base.metadata.sorted_tables)
)


async def clear_all_tables(engine: AsyncEngine) -> None:
    """Remove every committed row (children first) in one round trip.

    Tests commit for real (several need separate concurrent transactions, e.g. the upload
    race test), so savepoint rollback is not an option. DELETE beats TRUNCATE here: TRUNCATE
    has a fixed per-table cost (new files under an exclusive lock) that dominates when each
    test leaves only a handful of rows. Measured in TASK-02.08 (12 tables, pooled
    connection): TRUNCATE 60 ms, DELETE one-by-one 29 ms.
    """
    async with engine.begin() as conn:
        # Several statements, no parameters: psycopg sends them as one simple query.
        await conn.exec_driver_sql(_CLEAR_ALL_SQL)


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def test_settings(migrated_database_url: str) -> Settings:
    """Application settings pointing at the migrated scratch database (credentials from the
    environment / .env, exactly like the developer's own setup)."""
    base = Settings()
    scratch_name = make_url(migrated_database_url).database
    return base.model_copy(
        update={
            "database": base.database.model_copy(update={"name": scratch_name}),
            # A key namespace of its own per test (no counts leak between tests or runs), and
            # limits high enough that ordinary auth tests never hit them. Rate-limit tests
            # set their own low limits.
            "rate_limit": base.rate_limit.model_copy(
                update={
                    "key_prefix": f"cortex-test:{uuid.uuid4().hex}",
                    "login_per_ip_per_minute": 10_000,
                    "login_per_email_per_minute": 10_000,
                    "register_per_ip_per_hour": 10_000,
                }
            ),
        }
    )


@pytest.fixture
async def api_app(test_settings: Settings, db_engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    # Depends on db_engine so the tables are emptied after the test.
    application = create_app(test_settings)
    yield application
    await application.state.db_engine.dispose()
    await application.state.redis.aclose()


@pytest.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

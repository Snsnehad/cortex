"""Shared fixtures for integration tests that need their own PostgreSQL database.

* ``database_url``: an empty scratch database per test (migration tests).
* ``migrated_database_url``: one scratch database per test session, built by running the
  real Alembic migrations, so model tests exercise exactly the schema production gets
  (not ``metadata.create_all()``, which could hide a broken migration).

Scratch databases are dropped WITH (FORCE) afterwards; the development database is never
touched. Fixtures here are synchronous because Alembic commands call ``asyncio.run``.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.config import Settings

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

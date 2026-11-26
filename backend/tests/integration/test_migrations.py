"""Alembic migrations (TASK-02.02).

Every test runs against its own scratch database (created here, dropped with FORCE after),
never the development database.

Synchronous on purpose: Alembic commands call ``asyncio.run`` internally (migrations/env.py),
which cannot run inside an already-running event loop.
"""

import configparser
import contextlib
import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401  (register every model, exactly like env.py)
from app.config import Settings
from app.database.base import Base

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
VERSION_TABLE = "alembic_version"


@pytest.fixture
def database_url() -> Iterator[str]:
    """Create a uniquely named scratch database and drop it afterwards."""
    settings = Settings()
    db = settings.database
    name = f"cortex_migtest_{uuid.uuid4().hex[:10]}"
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


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = database_url
    config.attributes["skip_logging_setup"] = True
    return config


def table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names())
    finally:
        engine.dispose()


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision(config: Config) -> str | None:
    return ScriptDirectory.from_config(config).get_current_head()


# --- configuration --------------------------------------------------------------------------


def test_alembic_ini_contains_no_database_url() -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI, encoding="utf-8")

    assert not parser.has_option("alembic", "sqlalchemy.url"), (
        "credentials must come from CORTEX_* settings, not alembic.ini"
    )


def test_revision_history_has_a_single_head(alembic_config: Config) -> None:
    # Two branches that each add a migration produce two heads; that must be merged
    # explicitly (alembic merge) before deploying.
    assert len(ScriptDirectory.from_config(alembic_config).get_heads()) <= 1


# --- applying migrations --------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_round_trip(alembic_config: Config, database_url: str) -> None:
    expected_tables = set(Base.metadata.tables) | {VERSION_TABLE}

    command.upgrade(alembic_config, "head")
    assert table_names(database_url) == expected_tables
    assert current_revision(database_url) == head_revision(alembic_config)

    command.downgrade(alembic_config, "base")
    assert table_names(database_url) <= {VERSION_TABLE}, "downgrade left tables behind"
    assert current_revision(database_url) is None

    command.upgrade(alembic_config, "head")
    assert table_names(database_url) == expected_tables


def test_stairway_every_revision_up_down_up(alembic_config: Config, database_url: str) -> None:
    """Catches a broken downgrade() in any single revision, not just the whole chain."""
    script = ScriptDirectory.from_config(alembic_config)
    revisions = list(reversed(list(script.walk_revisions("base", "heads"))))

    for revision in revisions:
        command.upgrade(alembic_config, revision.revision)
        command.downgrade(alembic_config, revision.down_revision or "base")  # type: ignore[arg-type]
        command.upgrade(alembic_config, revision.revision)

    assert current_revision(database_url) == head_revision(alembic_config)


def test_models_and_migrations_are_in_sync(alembic_config: Config, database_url: str) -> None:
    """Fails when a model changes without a migration (or a migration without the model)."""
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == [], (
        f'models and migrations differ; run: scripts\\dev.cmd revision "..."\n{differences}'
    )


def test_offline_sql_generation(alembic_config: Config) -> None:
    """``alembic upgrade head --sql`` renders a reviewable SQL script without a database."""
    output = io.StringIO()
    alembic_config.output_buffer = output
    with contextlib.redirect_stdout(output):
        command.upgrade(alembic_config, "head", sql=True)

    script = output.getvalue()
    assert "BEGIN;" in script
    assert "COMMIT;" in script
    if head_revision(alembic_config) is not None:
        # The version table is only created once there is a revision to record.
        assert f"CREATE TABLE {VERSION_TABLE}" in script

"""Alembic environment for Cortex.

* Target metadata is ``Base.metadata`` after importing ``app.models``, which imports every
  model module, so autogenerate sees all tables.
* The database URL comes from, in order:
    1. ``config.attributes["database_url"]`` (set programmatically, e.g. by tests targeting a
       scratch database; attributes avoid configparser ``%`` interpolation of passwords),
    2. the application settings (``CORTEX_DATABASE__*``).
* Logging uses the application's structured pipeline, not alembic.ini logger sections.
"""

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.config import get_settings
from app.core.logging import configure_logging
from app.database.base import Base

config = context.config
target_metadata = Base.metadata

settings = get_settings()
if not config.attributes.get("skip_logging_setup", False):
    configure_logging(settings)


def _database_url() -> str:
    url = config.attributes.get("database_url")
    return str(url) if url else settings.database.url


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        # Detect column type and server-default changes too, not just added/removed columns.
        compare_type=True,
        compare_server_default=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade head --sql``)."""
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    # NullPool: a migration run is a one-off process; no pool to keep warm.
    engine = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        connect_args={"application_name": "cortex-migrations"},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    # psycopg's async mode needs the selector event loop on Windows (ADR-018).
    loop_factory = asyncio.SelectorEventLoop if os.name == "nt" else None
    asyncio.run(_run_async_migrations(), loop_factory=loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

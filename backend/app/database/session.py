"""Engine and session factory.

One ``AsyncEngine`` (and its connection pool) per process, created by the app factory and
disposed on shutdown. One ``AsyncSession`` per request, opened by the ``get_db_session``
dependency and always closed afterwards, which returns its connection to the pool.

Transaction rule (DEVELOPMENT_GUIDELINES §3): services decide when to commit; repositories
never commit. Anything not committed when the request ends is rolled back.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

# Connections older than this are replaced, so long-lived pools survive server-side idle
# timeouts and failovers (RDS, PgBouncer) without surfacing errors to requests.
POOL_RECYCLE_SECONDS = 30 * 60


def create_engine(settings: Settings, *, application_name: str = "cortex-api") -> AsyncEngine:
    """Build the process-wide engine. No connection is opened until first use."""
    db = settings.database
    return create_async_engine(
        db.url,
        pool_size=db.pool_size,
        max_overflow=db.max_overflow,
        # Test a pooled connection before handing it out; transparently replaces connections
        # killed by a database restart instead of failing the request.
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        echo=db.echo_sql,
        # Shows up in pg_stat_activity: which process holds which connection.
        connect_args={"application_name": application_name},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit. Expired attributes would
    # trigger lazy loads, which are not allowed under asyncio.
    return async_sessionmaker(engine, expire_on_commit=False)

"""Per-process runtime for Celery tasks: one event loop and one database engine per process.

Tasks are thin synchronous wrappers around the async services (ADR-004)::

    @shared_task(name="cortex.something")
    def something(job_id: str) -> None:
        run_async(_something, uuid.UUID(job_id))

    async def _something(ctx: WorkerContext, job_id: uuid.UUID) -> None:
        async with ctx.session_factory() as session:
            ...

Why not ``asyncio.run()`` per task: it creates a *new* event loop every time, and an async
connection pool's connections belong to the loop that opened them, so from the second task
on the pooled connections would be unusable. Instead each process keeps one
``asyncio.Runner`` (one loop) for its whole life, and the engine lives on that loop.

Why "per process": Celery's prefork pool forks child processes. A connection pool inherited
across ``fork`` shares sockets with the parent, which corrupts both. The context is
therefore created lazily *in the process that uses it*, and rebuilt if the process id
changes (a forked child never reuses its parent's context).
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Concatenate

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.database.session import create_engine, create_session_factory

logger = get_logger("cortex.worker")


@dataclass
class WorkerContext:
    pid: int
    settings: Settings
    runner: asyncio.Runner
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


_settings: Settings | None = None
_context: WorkerContext | None = None


def configure(settings: Settings) -> None:
    """Settings for contexts created from now on (called by ``create_celery``)."""
    global _settings
    _settings = settings


def current_settings() -> Settings:
    return _settings or get_settings()


def _loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    # psycopg's async mode cannot use Windows' default Proactor loop (ADR-018). Production
    # workers run on Linux (ADR-015); this only matters for tests on a Windows machine.
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None


def get_context() -> WorkerContext:
    global _context
    pid = os.getpid()
    if _context is None or _context.pid != pid:
        # A context from another pid was inherited through fork: drop it without closing
        # (closing would act on the parent's sockets).
        settings = current_settings()
        runner = asyncio.Runner(loop_factory=_loop_factory())
        engine = create_engine(settings, application_name="cortex-worker")
        _context = WorkerContext(
            pid=pid,
            settings=settings,
            runner=runner,
            engine=engine,
            session_factory=create_session_factory(engine),
        )
        logger.info("worker.context_created", pid=pid)
    return _context


def run_async[**P, T](
    func: Callable[Concatenate[WorkerContext, P], Awaitable[T]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run ``func(context, *args, **kwargs)`` on this process's event loop."""
    context = get_context()

    async def call() -> T:
        return await func(context, *args, **kwargs)

    return context.runner.run(call())


def shutdown() -> None:
    """Dispose of this process's engine and close its loop (worker process shutdown)."""
    global _context
    context, _context = _context, None
    if context is None or context.pid != os.getpid():
        return
    try:
        context.runner.run(context.engine.dispose())
    finally:
        context.runner.close()
    logger.info("worker.context_closed", pid=context.pid)

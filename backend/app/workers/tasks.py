"""Celery tasks. Each is a thin sync wrapper that runs an async function on the process's
event loop (``app.workers.runtime``). Delivery is at-least-once: every task is idempotent.

Tasks are ``shared_task``s so they bind to whichever Celery app is current (the production
app, or one built by a test with its own settings).
"""

from celery import shared_task
from sqlalchemy import text

from app.core.logging import get_logger
from app.workers.runtime import WorkerContext, run_async

logger = get_logger("cortex.worker")

PING_TASK = "cortex.ping"


async def _ping(context: WorkerContext) -> str:
    async with context.session_factory() as session:
        await session.execute(text("SELECT 1"))
    logger.info("worker.ping", pid=context.pid)
    return "pong"


@shared_task(name=PING_TASK)
def ping() -> str:
    """Health check: proves a worker can take a task and reach the database. Idempotent."""
    return run_async(_ping)

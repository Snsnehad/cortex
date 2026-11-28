"""Celery tasks. Each is a thin sync wrapper that runs an async function on the process's
event loop (``app.workers.runtime``). Delivery is at-least-once: every task is idempotent.

Tasks are ``shared_task``s so they bind to whichever Celery app is current (the production
app, or one built by a test with its own settings).
"""

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from anyio import to_thread
from celery import Task, shared_task
from sqlalchemy import text

from app.core.logging import get_logger
from app.models import JobStatus, JobType
from app.services.jobs import JobService
from app.workers.base import JobRun, PermanentError, execute_job_task
from app.workers.queue import INGEST_TASK, RECONCILE_TASK, CeleryJobQueue, JobQueue
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


# --- ingest (TASK-05.04 placeholder; the pipeline is Phase 6) -------------------------------

PROCESSING_NOT_AVAILABLE = "PROCESSING_NOT_AVAILABLE"


async def _ingest(run: JobRun) -> dict[str, Any] | None:
    # Until the Phase 6 pipeline exists, say so plainly instead of pretending: the document
    # fails with a clear code and can be reprocessed (TASK-05.05) once processing exists.
    raise PermanentError(
        PROCESSING_NOT_AVAILABLE,
        "Document processing is not available yet. Reprocess the document later.",
    )


@shared_task(bind=True, name=INGEST_TASK)
def ingest_document(self: Task[Any, Any], job_id: str) -> str:
    return execute_job_task(self, job_id, _ingest)


# --- reconciler (beat) --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    recovered_for_retry: int
    failed_lease_expired: int
    reenqueued: int
    enqueue_failures: int


async def reconcile(context: WorkerContext, queue: JobQueue) -> ReconcileReport:
    """Recover jobs whose worker died, and re-enqueue jobs whose message was lost.
    Idempotent: running it again right away finds nothing more to do."""
    worker = context.settings.worker
    async with context.session_factory() as session:
        recovered = await JobService(
            session, lease_seconds=worker.job_lease_seconds
        ).recover_expired_leases(limit=worker.reconcile_batch_size)
    async with context.session_factory() as session:
        stale = await JobService(
            session, lease_seconds=worker.job_lease_seconds
        ).take_stale_for_enqueue(
            queued_after_seconds=worker.stale_queued_after_seconds,
            retrying_after_seconds=worker.stale_retrying_after_seconds,
            limit=worker.reconcile_batch_size,
        )
    # A job recovered from an expired lease is enqueued at once (no need to wait).
    to_enqueue: dict[uuid.UUID, JobType] = {
        job.id: job.job_type for job in recovered if job.status is JobStatus.RETRYING
    }
    to_enqueue.update({job.id: job.job_type for job in stale})
    failures = 0
    for job_id, job_type in to_enqueue.items():
        if not await to_thread.run_sync(queue.enqueue, job_id, job_type):
            failures += 1
    report = ReconcileReport(
        recovered_for_retry=sum(job.status is JobStatus.RETRYING for job in recovered),
        failed_lease_expired=sum(job.status is JobStatus.FAILED for job in recovered),
        reenqueued=len(to_enqueue) - failures,
        enqueue_failures=failures,
    )
    logger.info("reconcile.done", **asdict(report))
    return report


@shared_task(bind=True, name=RECONCILE_TASK)
def reconcile_jobs(self: Task[Any, Any]) -> dict[str, int]:
    return asdict(run_async(reconcile, CeleryJobQueue(self.app)))

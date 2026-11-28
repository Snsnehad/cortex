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
from sqlalchemy import select, text

from app.core.logging import get_logger
from app.models import Document, JobStatus, JobType
from app.repositories.jobs import JobRepository
from app.services.jobs import JobService
from app.storage import StorageUnavailableError
from app.workers.base import (
    JobLostError,
    JobRun,
    PermanentError,
    TransientError,
    execute_job_task,
)
from app.workers.queue import (
    DELETE_TASK,
    INGEST_TASK,
    RECONCILE_TASK,
    CeleryJobQueue,
    JobQueue,
)
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


# --- delete (TASK-05.05) ----------------------------------------------------------------------


async def _delete_document(run: JobRun) -> None:
    """Remove a DELETING document: its stored file, then its rows. Every step is idempotent,
    so a retry after a crash simply repeats what is left.

    Order matters: rows last. Until they are gone the document is still DELETING (invisible),
    and the job can be retried; deleting the rows first would lose track of the file.
    (Phase 7 adds the vector purge here, before the rows, so no search returns it.)"""
    claim = run.claim
    async with run.session_factory() as session:
        document = await session.scalar(
            select(Document).where(
                Document.id == claim.document_id,
                Document.organization_id == claim.organization_id,
            )
        )
        if document is None:  # already gone (the job would have gone with it)
            run.mark_removed_with_document()
            return
        storage_key, knowledge_base_id = document.storage_key, document.knowledge_base_id
    try:
        await run.storage.delete(storage_key)  # a missing file is fine (idempotent)
    except StorageUnavailableError as exc:
        raise TransientError("STORAGE_UNAVAILABLE", "File storage is unavailable.") from exc
    async with run.session_factory() as session:
        jobs = JobRepository(session)
        removed = await jobs.delete_document_if_held(
            claim.job_id, claim.attempt, claim.organization_id, claim.document_id
        )
        if not removed:  # no longer our job: write nothing
            await session.rollback()
            raise JobLostError(str(claim.job_id))
        # Its knowledge base, if deleted and now empty, goes too.
        await jobs.delete_empty_deleted_knowledge_bases(
            organization_id=claim.organization_id, knowledge_base_id=knowledge_base_id
        )
        await session.commit()
    logger.info("document.deleted", document_id=str(claim.document_id))
    run.mark_removed_with_document()


@shared_task(bind=True, name=DELETE_TASK)
def delete_document(self: Task[Any, Any], job_id: str) -> str:
    return execute_job_task(self, job_id, _delete_document)


# --- reconciler (beat) --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    recovered_for_retry: int
    failed_lease_expired: int
    reenqueued: int
    enqueue_failures: int
    knowledge_bases_removed: int = 0


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
    async with context.session_factory() as session:
        # Deleted knowledge bases whose last document is gone (or that were empty).
        removed_kbs = await JobRepository(session).delete_empty_deleted_knowledge_bases(
            limit=worker.reconcile_batch_size
        )
        await session.commit()
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
        knowledge_bases_removed=removed_kbs,
    )
    logger.info("reconcile.done", **asdict(report))
    return report


@shared_task(bind=True, name=RECONCILE_TASK)
def reconcile_jobs(self: Task[Any, Any]) -> dict[str, int]:
    return asdict(run_async(reconcile, CeleryJobQueue(self.app)))

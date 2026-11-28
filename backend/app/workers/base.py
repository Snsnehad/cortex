"""Running a job's work with retries (TASK-05.03).

Every processing task follows the same shape, implemented once here::

    claim → work (heartbeats on the way) → succeed
                 └─ error → fail: retry later, or give up

Errors decide what happens next:

* ``TransientError``: worth retrying (network, timeouts, 5xx, rate limits, a busy
  service). The job goes back to RETRYING and is re-enqueued after a backoff delay, while
  attempts remain; on the last attempt it fails for good.
* ``PermanentError(code, message)``: retrying cannot help (corrupt, encrypted or
  unsupported file). The job and its document fail at once with that code and message.
* ``SoftTimeLimitExceeded``: treated as transient (``TIME_LIMIT_EXCEEDED``).
* Anything else is a bug or an unforeseen failure: treated as transient
  (``INTERNAL_ERROR``), its traceback logged **once**, and its message *not* stored. An
  exception's text can contain document content, which never goes into the database.
* ``JobLostError``: raised by ``JobRun.heartbeat`` when the job was taken back (lease
  expired, cancelled). The work stops and **nothing is written**: the job belongs to
  someone else now.

Attempts are counted by the job row, not by Celery's retry counter: PostgreSQL stays the
source of truth, and a redelivered message cannot add attempts.
"""

import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import WorkerSettings
from app.core.logging import get_logger
from app.models import JobStage, JobStatus
from app.services.jobs import Claim, JobService
from app.storage import StorageBackend
from app.workers.runtime import WorkerContext, run_async

logger = get_logger("cortex.worker")

INTERNAL_ERROR = "INTERNAL_ERROR"
TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"


class TransientError(Exception):
    """A failure worth retrying. ``code`` is recorded on the job (e.g. ``LLM_TIMEOUT``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PermanentError(Exception):
    """A failure retrying cannot fix. Its message is shown to users: keep it plain and free
    of document content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JobLostError(Exception):
    """The job is no longer ours (lease expired or cancelled): stop, write nothing."""


class Outcome(StrEnum):
    SKIPPED = "SKIPPED"  # not claimable: taken, finished or cancelled
    SUCCEEDED = "SUCCEEDED"
    RETRY = "RETRY"  # back to RETRYING; re-enqueue after `retry_in_seconds`
    FAILED = "FAILED"
    LOST = "LOST"  # taken back while we worked; nothing written


@dataclass(frozen=True, slots=True)
class JobResult:
    outcome: Outcome
    retry_in_seconds: float | None = None
    error_code: str | None = None


def backoff_seconds(
    attempt: int, settings: WorkerSettings, rng: random.Random | None = None
) -> float:
    """Full jitter: uniform in [0, min(max, base * 2 ** (attempt - 1))]."""
    ceiling = min(
        settings.retry_backoff_max_seconds,
        settings.retry_backoff_base_seconds * 2 ** max(attempt - 1, 0),
    )
    return (rng or random).uniform(0, ceiling)


class JobRun:
    """What the work function gets: the claim, a heartbeat that also reports progress, a
    session factory and the file storage."""

    def __init__(
        self,
        claim: Claim,
        factory: async_sessionmaker[AsyncSession],
        lease_seconds: int,
        storage: StorageBackend | None = None,
    ) -> None:
        self.claim = claim
        self.session_factory = factory
        self._factory = factory
        self._lease_seconds = lease_seconds
        self._storage = storage
        self.removed_with_document = False

    @property
    def storage(self) -> StorageBackend:
        if self._storage is None:
            raise RuntimeError("this job run has no storage")
        return self._storage

    def mark_removed_with_document(self) -> None:
        """The work deleted the document row, and with it (ON DELETE CASCADE) this job's
        row: there is nothing left to mark SUCCEEDED, and that is the success."""
        self.removed_with_document = True

    async def heartbeat(self, stage: JobStage | None = None) -> None:
        """Renew the lease; call it well within the lease. Raises JobLostError if the job
        was taken back, which stops the work."""
        async with self._factory() as session:
            renewed = await JobService(session, lease_seconds=self._lease_seconds).heartbeat(
                self.claim, stage=stage
            )
        if not renewed:
            raise JobLostError(str(self.claim.job_id))


Work = Callable[[JobRun], Awaitable[dict[str, Any] | None]]


async def run_job(
    job_id: uuid.UUID,
    work: Work,
    *,
    factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
    rng: random.Random | None = None,
    storage: StorageBackend | None = None,
) -> JobResult:
    """Claim ``job_id``, run ``work``, and record the outcome. Never raises for job
    failures; the returned ``JobResult`` tells the caller whether to re-enqueue."""

    def jobs(session: AsyncSession) -> JobService:
        return JobService(session, lease_seconds=settings.job_lease_seconds)

    async with factory() as session:
        claim = await jobs(session).claim(job_id)
    if claim is None:
        return JobResult(Outcome.SKIPPED)

    held: Claim = claim  # non-optional from here on (also inside the closure below)

    async def fail(code: str, message: str, *, retryable: bool) -> JobResult:
        async with factory() as session:
            status = await jobs(session).fail(
                held, error_code=code, error_message=message, retryable=retryable
            )
        if status is None:  # taken back meanwhile: nothing was written
            return JobResult(Outcome.LOST, error_code=code)
        if status is JobStatus.RETRYING:
            delay = backoff_seconds(held.attempt, settings, rng)
            return JobResult(Outcome.RETRY, retry_in_seconds=delay, error_code=code)
        return JobResult(Outcome.FAILED, error_code=code)

    run = JobRun(claim, factory, settings.job_lease_seconds, storage)
    try:
        metrics = await work(run)
    except JobLostError:
        logger.warning("job.lost_during_work", job_id=str(job_id), attempt=claim.attempt)
        return JobResult(Outcome.LOST)
    except PermanentError as exc:
        return await fail(exc.code, exc.message, retryable=False)
    except TransientError as exc:
        return await fail(exc.code, exc.message, retryable=True)
    except SoftTimeLimitExceeded:
        return await fail(
            TIME_LIMIT_EXCEEDED, "Processing took too long and was stopped.", retryable=True
        )
    except Exception:
        # Logged once, with the traceback; the message is NOT stored (it may contain
        # document content). The job keeps a generic code.
        logger.exception("job.unexpected_error", job_id=str(job_id), attempt=claim.attempt)
        return await fail(
            INTERNAL_ERROR, "An unexpected error occurred while processing.", retryable=True
        )

    if run.removed_with_document:
        logger.info("job.removed_with_document", job_id=str(job_id))
        return JobResult(Outcome.SUCCEEDED)
    async with factory() as session:
        succeeded = await jobs(session).succeed(held, metrics=metrics)
    return JobResult(Outcome.SUCCEEDED if succeeded else Outcome.LOST)


async def _run_in_worker(context: WorkerContext, job_id: uuid.UUID, work: Work) -> JobResult:
    return await run_job(
        job_id,
        work,
        factory=context.session_factory,
        settings=context.settings.worker,
        storage=context.storage,
    )


def execute_job_task(task: Task[Any, Any], job_id: str, work: Work) -> str:
    """The body of every job task::

        @shared_task(bind=True, name="cortex.ingest_document")
        def ingest_document(self, job_id: str) -> str:
            return execute_job_task(self, job_id, _ingest)

    On RETRY the task enqueues itself again with the backoff delay (a *new* message; this
    one is acknowledged normally). If the worker dies between recording RETRYING and
    enqueueing, the reconciler (TASK-05.04) re-enqueues RETRYING jobs it finds idle.
    """
    result = run_async(_run_in_worker, uuid.UUID(job_id), work)
    if result.outcome is Outcome.RETRY:
        task.apply_async(args=(job_id,), countdown=result.retry_in_seconds or 0.0)
        logger.info(
            "job.retry_scheduled",
            job_id=job_id,
            retry_in_seconds=round(result.retry_in_seconds or 0.0, 1),
            error_code=result.error_code,
        )
    return result.outcome.value

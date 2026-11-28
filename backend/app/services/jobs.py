"""Processing-job lifecycle for workers (TASK-05.02).

States and the only allowed transitions (enforced by the conditional UPDATEs in
``JobRepository``; ``TRANSITIONS`` documents them and the tests check every pair)::

    QUEUED ──claim──▶ RUNNING ──succeed──▶ SUCCEEDED
      │                │  │ ▲
      │                │  │ └──claim── RETRYING ◀──fail (retryable, attempts left)──┘
      │                │  └──fail (permanent, or no attempts left)──▶ FAILED
      └────cancel──────┴──────────────────cancel──────────────────▶ CANCELLED

A worker's use::

    claim = await jobs.claim(job_id)
    if claim is None:          # someone else has it, or it is finished: do nothing
        return
    ... work, calling `await jobs.heartbeat(claim, stage=...)` regularly; if it returns
        False the job was taken back (lease expired): stop without writing anything ...
    await jobs.succeed(claim, metrics=...)    # or: await jobs.fail(claim, ...)

Every write after the claim is **fenced** by the attempt number the claim returned, so a
stalled worker whose lease expired cannot overwrite the result of the worker that took the
job over. For INGEST jobs the document's status moves in the same transaction: PROCESSING on
claim, READY on success, FAILED when the job fails for good (a retry keeps it PROCESSING).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import DocumentStatus, JobStage, JobStatus, JobType, ProcessingJob
from app.repositories.jobs import JobRepository

logger = get_logger("cortex.jobs")

MAX_ERROR_MESSAGE = 1000  # processing_jobs.error_message / documents.error_message

TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RETRYING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.RETRYING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Claim:
    """Proof that this worker holds a job; ``attempt`` is the fencing token."""

    job_id: uuid.UUID
    document_id: uuid.UUID
    organization_id: uuid.UUID
    job_type: JobType
    attempt: int
    lease_expires_at: datetime


class JobService:
    def __init__(self, session: AsyncSession, *, lease_seconds: int) -> None:
        self._session = session
        self._jobs = JobRepository(session)
        self._lease_seconds = lease_seconds

    async def claim(self, job_id: uuid.UUID) -> Claim | None:
        job = await self._jobs.claim(job_id, lease_seconds=self._lease_seconds)
        if job is None:
            await self._session.commit()  # nothing changed; end the transaction
            logger.info("job.claim_skipped", job_id=str(job_id))
            return None
        if job.lease_expires_at is None:  # the claim UPDATE always sets it
            raise RuntimeError("claimed job has no lease")
        if job.job_type is JobType.INGEST:
            await self._jobs.set_document_status(job, DocumentStatus.PROCESSING)
        claim = Claim(
            job_id=job.id,
            document_id=job.document_id,
            organization_id=job.organization_id,
            job_type=job.job_type,
            attempt=job.attempts,
            lease_expires_at=job.lease_expires_at,
        )
        await self._session.commit()
        logger.info("job.claimed", job_id=str(job.id), attempt=claim.attempt)
        return claim

    async def heartbeat(self, claim: Claim, *, stage: JobStage | None = None) -> bool:
        """Renew the lease (and record progress). False: the job is no longer ours."""
        job = await self._jobs.renew_lease(
            claim.job_id, claim.attempt, lease_seconds=self._lease_seconds, stage=stage
        )
        await self._session.commit()
        if job is None:
            logger.warning("job.lease_lost", job_id=str(claim.job_id), attempt=claim.attempt)
        return job is not None

    async def succeed(self, claim: Claim, *, metrics: dict[str, Any] | None = None) -> bool:
        job = await self._jobs.update_held(
            claim.job_id,
            claim.attempt,
            status=JobStatus.SUCCEEDED,
            finished_at=func.now(),
            lease_expires_at=None,
            stage=None,
            metrics=metrics,
        )
        if job is not None and job.job_type is JobType.INGEST:
            await self._jobs.set_document_status(
                job,
                DocumentStatus.READY,
                processed_at=func.now(),
                error_code=None,
                error_message=None,
            )
        await self._session.commit()
        self._log_outcome(claim, job, "job.succeeded")
        return job is not None

    async def fail(
        self, claim: Claim, *, error_code: str, error_message: str, retryable: bool
    ) -> JobStatus | None:
        """RETRYING if ``retryable`` and attempts remain, else FAILED. Returns the new
        status, or None if the job was no longer ours (nothing written)."""
        message = error_message[:MAX_ERROR_MESSAGE]
        # Retry or give up is decided by the database in the same UPDATE as the transition,
        # from the job's own attempts/max_attempts (no read-then-write).
        will_retry = ProcessingJob.attempts < ProcessingJob.max_attempts
        if retryable:
            status: Any = case((will_retry, JobStatus.RETRYING.value), else_=JobStatus.FAILED.value)
            finished_at: Any = case((will_retry, None), else_=func.now())
        else:
            status, finished_at = JobStatus.FAILED, func.now()
        job = await self._jobs.update_held(
            claim.job_id,
            claim.attempt,
            status=status,
            finished_at=finished_at,
            lease_expires_at=None,
            stage=None,
            error_code=error_code,
            error_message=message,
        )
        if job is not None and job.status is JobStatus.FAILED and job.job_type is JobType.INGEST:
            await self._jobs.set_document_status(
                job, DocumentStatus.FAILED, error_code=error_code, error_message=message
            )
        await self._session.commit()
        self._log_outcome(
            claim, job, "job.failed", error_code=error_code, status=job.status if job else None
        )
        return job.status if job is not None else None

    async def recover_expired_leases(self, *, limit: int) -> list[ProcessingJob]:
        """Reconciler: jobs whose worker died. INGEST documents of jobs that failed for
        good are marked FAILED in the same transaction."""
        jobs = await self._jobs.recover_expired_leases(limit=limit)
        for job in jobs:
            if job.status is JobStatus.FAILED and job.job_type is JobType.INGEST:
                await self._jobs.set_document_status(
                    job,
                    DocumentStatus.FAILED,
                    error_code=job.error_code,
                    error_message=job.error_message,
                )
        await self._session.commit()
        return jobs

    async def take_stale_for_enqueue(
        self, *, queued_after_seconds: int, retrying_after_seconds: int, limit: int
    ) -> list[ProcessingJob]:
        jobs = await self._jobs.touch_stale(
            queued_after=timedelta(seconds=queued_after_seconds),
            retrying_after=timedelta(seconds=retrying_after_seconds),
            limit=limit,
        )
        await self._session.commit()
        return jobs

    async def cancel(self, job_id: uuid.UUID) -> bool:
        job = await self._jobs.cancel_active(job_id)
        await self._session.commit()
        return job is not None

    @staticmethod
    def _log_outcome(claim: Claim, job: ProcessingJob | None, event: str, **extra: Any) -> None:
        if job is None:
            logger.warning(
                "job.write_refused_not_holder", job_id=str(claim.job_id), attempt=claim.attempt
            )
            return
        logger.info(event, job_id=str(claim.job_id), attempt=claim.attempt, **extra)

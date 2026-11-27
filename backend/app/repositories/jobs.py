"""Data access for processing jobs: every state change is ONE conditional UPDATE.

The ``WHERE`` clause of each statement *is* the state machine: an update matches only when
the job is in a state the transition allows (and, for a running job, only for the worker
that holds it). Zero rows updated means "not allowed now"; nothing is ever read first and
written later, so two workers can never both act on the same job. Repositories never
commit.

Fencing: a claim increments ``attempts`` and returns the new value. Every later write by
that worker requires ``status = RUNNING AND attempts = <its value>``. If a worker stalls,
loses its lease and the job is claimed again, the old worker's late writes match nothing.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ACTIVE_JOB_STATUSES,
    Document,
    DocumentStatus,
    JobStage,
    JobStatus,
    ProcessingJob,
)

CLAIMABLE = (JobStatus.QUEUED, JobStatus.RETRYING)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, job_id: uuid.UUID, *, lease_seconds: int) -> ProcessingJob | None:
        """QUEUED/RETRYING → RUNNING, if attempts remain and the document is not being
        deleted. Returns the claimed job, or None if it is not claimable (anymore)."""
        document_live = exists().where(
            Document.id == ProcessingJob.document_id,
            Document.organization_id == ProcessingJob.organization_id,
            Document.status != DocumentStatus.DELETING,
        )
        job: ProcessingJob | None = await self._session.scalar(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status.in_(CLAIMABLE),
                ProcessingJob.attempts < ProcessingJob.max_attempts,
                document_live,
            )
            .values(
                status=JobStatus.RUNNING,
                attempts=ProcessingJob.attempts + 1,
                started_at=func.now(),
                lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
                stage=None,
                error_code=None,
                error_message=None,
            )
            .returning(ProcessingJob)
            .execution_options(populate_existing=True)
        )
        return job

    async def update_held(
        self, job_id: uuid.UUID, attempt: int, **values: Any
    ) -> ProcessingJob | None:
        """Update a RUNNING job only if this worker (``attempt``) still holds it."""
        job: ProcessingJob | None = await self._session.scalar(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status == JobStatus.RUNNING,
                ProcessingJob.attempts == attempt,
            )
            .values(**values)
            .returning(ProcessingJob)
            .execution_options(populate_existing=True)
        )
        return job

    async def renew_lease(
        self, job_id: uuid.UUID, attempt: int, *, lease_seconds: int, stage: JobStage | None
    ) -> ProcessingJob | None:
        values: dict[str, Any] = {"lease_expires_at": func.now() + timedelta(seconds=lease_seconds)}
        if stage is not None:
            values["stage"] = stage
        return await self.update_held(job_id, attempt, **values)

    async def cancel_active(self, job_id: uuid.UUID) -> ProcessingJob | None:
        job: ProcessingJob | None = await self._session.scalar(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .values(status=JobStatus.CANCELLED, finished_at=func.now(), lease_expires_at=None)
            .returning(ProcessingJob)
            .execution_options(populate_existing=True)
        )
        return job

    async def set_document_status(
        self, job: ProcessingJob, status: DocumentStatus, **values: Any
    ) -> None:
        """Move the job's document along with it (never a document that is being deleted)."""
        await self._session.execute(
            update(Document)
            .where(
                Document.id == job.document_id,
                Document.organization_id == job.organization_id,
                Document.status != DocumentStatus.DELETING,
            )
            .values(status=status, **values)
        )

    async def get(self, job_id: uuid.UUID) -> ProcessingJob | None:
        job: ProcessingJob | None = await self._session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .execution_options(populate_existing=True)
        )
        return job

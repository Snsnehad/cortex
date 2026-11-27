"""Job lifecycle: atomic claim, fenced writes, transitions, document status (TASK-05.02)."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentStatus, JobStage, JobStatus, JobType, ProcessingJob
from app.services.jobs import TRANSITIONS, Claim, JobService
from tests.integration.api_helpers import team
from tests.integration.db_helpers import add_all_and_commit
from tests.integration.factories import create_document, create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
LEASE = 300


async def new_job(
    factory: Factory,
    *,
    status: JobStatus = JobStatus.QUEUED,
    job_type: JobType = JobType.INGEST,
    attempts: int = 0,
    max_attempts: int = 3,
) -> ProcessingJob:
    t = await team(factory)
    document = await create_document(factory, await create_kb(factory, t["org"]))
    job = ProcessingJob(
        organization_id=document.organization_id,
        document_id=document.id,
        job_type=job_type,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
    )
    await add_all_and_commit(factory, job)
    return job


async def call(factory: Factory, method: str, *args: Any, **kwargs: Any) -> Any:
    """Each call in its own session, as separate worker processes would."""
    async with factory() as session:
        return await getattr(JobService(session, lease_seconds=LEASE), method)(*args, **kwargs)


async def load(factory: Factory, job: ProcessingJob) -> tuple[ProcessingJob, Document]:
    async with factory() as session:
        fresh = await session.get(ProcessingJob, job.id)
        document = await session.get(Document, job.document_id)
        assert fresh is not None
        assert document is not None
        return fresh, document


async def set_job(factory: Factory, job: ProcessingJob, **values: Any) -> None:
    async with factory() as session:
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job.id).values(**values)
        )
        await session.commit()


async def set_document(factory: Factory, job: ProcessingJob, status: DocumentStatus) -> None:
    async with factory() as session:
        await session.execute(
            update(Document).where(Document.id == job.document_id).values(status=status)
        )
        await session.commit()


# --- claim ----------------------------------------------------------------------------------


async def test_claim_takes_the_job_and_starts_the_document(session_factory: Factory) -> None:
    job = await new_job(session_factory)

    claim = await call(session_factory, "claim", job.id)

    assert isinstance(claim, Claim)
    assert (claim.job_id, claim.document_id, claim.attempt) == (job.id, job.document_id, 1)
    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.attempts) == (JobStatus.RUNNING, 1)
    assert fresh.started_at is not None
    assert fresh.lease_expires_at is not None
    lease = fresh.lease_expires_at - fresh.started_at
    assert abs(lease - timedelta(seconds=LEASE)) < timedelta(seconds=1)  # database clock
    assert document.status is DocumentStatus.PROCESSING


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ({"status": JobStatus.RUNNING}, "already running"),
        ({"status": JobStatus.SUCCEEDED}, "finished"),
        ({"status": JobStatus.FAILED}, "failed for good"),
        ({"status": JobStatus.CANCELLED}, "cancelled"),
        ({"status": JobStatus.RETRYING, "attempts": 3}, "no attempts left"),
    ],
)
async def test_unclaimable_jobs_are_left_alone(
    session_factory: Factory, setup: dict[str, Any], reason: str
) -> None:
    job = await new_job(session_factory, **setup)
    before, _ = await load(session_factory, job)

    assert await call(session_factory, "claim", job.id) is None, reason

    after, _ = await load(session_factory, job)
    assert (after.status, after.attempts) == (before.status, before.attempts)


async def test_a_document_being_deleted_is_not_processed(session_factory: Factory) -> None:
    job = await new_job(session_factory)
    await set_document(session_factory, job, DocumentStatus.DELETING)

    assert await call(session_factory, "claim", job.id) is None
    assert (await load(session_factory, job))[0].status is JobStatus.QUEUED


async def test_concurrent_claims_have_exactly_one_winner_per_job(
    session_factory: Factory,
) -> None:
    """Many workers race for many jobs; each job must be claimed exactly once."""
    jobs = [await new_job(session_factory) for _ in range(8)]

    results = await asyncio.gather(
        *(call(session_factory, "claim", job.id) for job in jobs for _ in range(4))
    )

    winners = [r for r in results if r is not None]
    assert sorted(str(c.job_id) for c in winners) == sorted(str(j.id) for j in jobs)
    for job in jobs:
        assert (await load(session_factory, job))[0].attempts == 1


# --- heartbeat and fencing ------------------------------------------------------------------


async def test_heartbeat_renews_the_lease_and_records_progress(session_factory: Factory) -> None:
    job = await new_job(session_factory)
    claim = await call(session_factory, "claim", job.id)
    await set_job(session_factory, job, lease_expires_at=func.now())  # about to expire

    assert await call(session_factory, "heartbeat", claim, stage=JobStage.EMBEDDING) is True

    fresh, _ = await load(session_factory, job)
    assert fresh.stage is JobStage.EMBEDDING
    assert fresh.lease_expires_at is not None
    assert fresh.started_at is not None
    assert fresh.lease_expires_at > fresh.started_at + timedelta(seconds=LEASE - 5)


async def test_a_stale_worker_cannot_touch_a_job_that_was_taken_over(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    old = await call(session_factory, "claim", job.id)
    # The old worker stalls; its lease is considered expired and the job is retried and
    # claimed by another worker (attempt 2).
    await set_job(session_factory, job, status=JobStatus.RETRYING, lease_expires_at=None)
    new = await call(session_factory, "claim", job.id)
    assert (old.attempt, new.attempt) == (1, 2)

    assert await call(session_factory, "heartbeat", old) is False
    assert await call(session_factory, "succeed", old) is False
    assert (
        await call(session_factory, "fail", old, error_code="X", error_message="x", retryable=False)
        is None
    )

    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.attempts, fresh.error_code) == (JobStatus.RUNNING, 2, None)
    assert document.status is DocumentStatus.PROCESSING
    assert await call(session_factory, "succeed", new) is True  # the holder still can


# --- outcomes -------------------------------------------------------------------------------


async def test_success_finishes_the_job_and_readies_the_document(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    claim = await call(session_factory, "claim", job.id)

    assert await call(session_factory, "succeed", claim, metrics={"chunks": 12}) is True

    fresh, document = await load(session_factory, job)
    assert fresh.status is JobStatus.SUCCEEDED
    assert fresh.finished_at is not None
    assert (fresh.lease_expires_at, fresh.stage, fresh.metrics) == (None, None, {"chunks": 12})
    assert document.status is DocumentStatus.READY
    assert document.processed_at is not None


async def test_retryable_failure_goes_back_to_the_queue(session_factory: Factory) -> None:
    job = await new_job(session_factory, max_attempts=3)
    claim = await call(session_factory, "claim", job.id)

    status = await call(
        session_factory, "fail", claim, error_code="LLM_TIMEOUT", error_message="t", retryable=True
    )

    assert status is JobStatus.RETRYING
    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.error_code, fresh.finished_at) == (
        JobStatus.RETRYING,
        "LLM_TIMEOUT",
        None,
    )
    assert document.status is DocumentStatus.PROCESSING  # still being worked on
    retry = await call(session_factory, "claim", job.id)
    assert retry.attempt == 2
    assert (await load(session_factory, job))[0].error_code is None  # cleared on the new run


async def test_retryable_failure_on_the_last_attempt_is_final(session_factory: Factory) -> None:
    job = await new_job(session_factory, max_attempts=1)
    claim = await call(session_factory, "claim", job.id)

    status = await call(
        session_factory, "fail", claim, error_code="LLM_TIMEOUT", error_message="t", retryable=True
    )

    assert status is JobStatus.FAILED
    fresh, document = await load(session_factory, job)
    assert fresh.finished_at is not None
    assert (document.status, document.error_code) == (DocumentStatus.FAILED, "LLM_TIMEOUT")


async def test_permanent_failure_is_final_at_once(session_factory: Factory) -> None:
    job = await new_job(session_factory, max_attempts=5)
    claim = await call(session_factory, "claim", job.id)

    status = await call(
        session_factory,
        "fail",
        claim,
        error_code="ENCRYPTED_PDF",
        error_message="The PDF is password-protected.",
        retryable=False,
    )

    assert status is JobStatus.FAILED
    _, document = await load(session_factory, job)
    assert (document.status, document.error_code, document.error_message) == (
        DocumentStatus.FAILED,
        "ENCRYPTED_PDF",
        "The PDF is password-protected.",
    )


async def test_long_error_messages_are_truncated(session_factory: Factory) -> None:
    job = await new_job(session_factory)
    claim = await call(session_factory, "claim", job.id)

    await call(
        session_factory, "fail", claim, error_code="X", error_message="e" * 5000, retryable=False
    )

    fresh, document = await load(session_factory, job)
    assert fresh.error_message is not None
    assert document.error_message is not None
    assert len(fresh.error_message) == len(document.error_message) == 1000


async def test_a_document_being_deleted_keeps_that_status(session_factory: Factory) -> None:
    job = await new_job(session_factory)
    claim = await call(session_factory, "claim", job.id)
    await set_document(session_factory, job, DocumentStatus.DELETING)  # deleted mid-run

    await call(session_factory, "succeed", claim)

    assert (await load(session_factory, job))[1].status is DocumentStatus.DELETING


async def test_non_ingest_jobs_leave_the_document_status_alone(session_factory: Factory) -> None:
    job = await new_job(session_factory, job_type=JobType.REINDEX)
    await set_document(session_factory, job, DocumentStatus.READY)

    claim = await call(session_factory, "claim", job.id)
    assert (await load(session_factory, job))[1].status is DocumentStatus.READY
    await call(session_factory, "fail", claim, error_code="X", error_message="x", retryable=False)
    assert (await load(session_factory, job))[1].status is DocumentStatus.READY


async def test_cancel(session_factory: Factory) -> None:
    job = await new_job(session_factory)
    claim = await call(session_factory, "claim", job.id)

    assert await call(session_factory, "cancel", job.id) is True
    assert await call(session_factory, "cancel", job.id) is False  # already final
    assert await call(session_factory, "succeed", claim) is False  # the worker lost it
    assert (await load(session_factory, job))[0].status is JobStatus.CANCELLED


# --- the transition table is what the database enforces -------------------------------------


@pytest.mark.parametrize("start", list(JobStatus))
async def test_every_transition_matches_the_table(
    session_factory: Factory, start: JobStatus
) -> None:
    allowed = TRANSITIONS[start]
    outcomes: dict[JobStatus, bool] = {}

    for target in (JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        job = await new_job(session_factory, status=start, attempts=1, max_attempts=3)
        holder = Claim(
            job.id, job.document_id, job.organization_id, job.job_type, 1, job.created_at
        )
        if target is JobStatus.RUNNING:
            done = await call(session_factory, "claim", job.id) is not None
        elif target is JobStatus.SUCCEEDED:
            done = await call(session_factory, "succeed", holder)
        elif target is JobStatus.FAILED:
            done = (
                await call(
                    session_factory,
                    "fail",
                    holder,
                    error_code="X",
                    error_message="x",
                    retryable=False,
                )
                is not None
            )
        else:
            done = await call(session_factory, "cancel", job.id)
        outcomes[target] = bool(done)
        status = (await load(session_factory, job))[0].status
        assert status is (target if done else start)

    assert {t for t, ok in outcomes.items() if ok} == allowed - {JobStatus.RETRYING}

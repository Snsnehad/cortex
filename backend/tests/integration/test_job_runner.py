"""run_job: claim → work → succeed / retry / fail, against the real database (TASK-05.03)."""

import random
from typing import Any

import pytest
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import WorkerSettings
from app.models import Document, DocumentStatus, JobStage, JobStatus, ProcessingJob
from app.workers.base import (
    INTERNAL_ERROR,
    TIME_LIMIT_EXCEEDED,
    JobRun,
    Outcome,
    PermanentError,
    TransientError,
    run_job,
)
from tests.integration.api_helpers import team
from tests.integration.db_helpers import add_all_and_commit
from tests.integration.factories import create_document, create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
SETTINGS = WorkerSettings(retry_backoff_base_seconds=10, retry_backoff_max_seconds=600)


async def new_job(factory: Factory, *, max_attempts: int = 3) -> ProcessingJob:
    t = await team(factory)
    document = await create_document(factory, await create_kb(factory, t["org"]))
    job = ProcessingJob(
        organization_id=document.organization_id,
        document_id=document.id,
        job_type="INGEST",
        status=JobStatus.QUEUED,
        max_attempts=max_attempts,
    )
    await add_all_and_commit(factory, job)
    return job


async def run(factory: Factory, job: ProcessingJob, work: Any) -> Any:
    return await run_job(job.id, work, factory=factory, settings=SETTINGS, rng=random.Random(1))


async def load(factory: Factory, job: ProcessingJob) -> tuple[ProcessingJob, Document]:
    async with factory() as session:
        fresh = await session.get(ProcessingJob, job.id)
        document = await session.get(Document, job.document_id)
        assert fresh is not None
        assert document is not None
        return fresh, document


def raising(error: BaseException) -> Any:
    async def work(run: JobRun) -> None:
        raise error

    return work


async def test_success_records_metrics_and_readies_the_document(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    stages: list[JobStage | None] = []

    async def work(run: JobRun) -> dict[str, Any]:
        await run.heartbeat(JobStage.EXTRACTING)
        stages.append((await load(session_factory, job))[0].stage)
        return {"pages": 3}

    result = await run(session_factory, job, work)

    assert result.outcome is Outcome.SUCCEEDED
    assert stages == [JobStage.EXTRACTING]
    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.metrics) == (JobStatus.SUCCEEDED, {"pages": 3})
    assert document.status is DocumentStatus.READY


async def test_transient_failure_retries_with_backoff_then_succeeds(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    calls = 0

    async def flaky(run: JobRun) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientError("LLM_TIMEOUT", "The embedding service timed out.")

    first = await run(session_factory, job, flaky)
    assert first.outcome is Outcome.RETRY
    assert first.error_code == "LLM_TIMEOUT"
    assert first.retry_in_seconds is not None
    assert 0 <= first.retry_in_seconds <= 10  # attempt 1: ceiling = base
    assert (await load(session_factory, job))[0].status is JobStatus.RETRYING

    second = await run(session_factory, job, flaky)

    assert second.outcome is Outcome.SUCCEEDED
    fresh, _ = await load(session_factory, job)
    assert (fresh.status, fresh.attempts, fresh.error_code) == (JobStatus.SUCCEEDED, 2, None)


async def test_transient_failure_on_the_last_attempt_is_final(session_factory: Factory) -> None:
    job = await new_job(session_factory, max_attempts=1)

    result = await run(session_factory, job, raising(TransientError("LLM_TIMEOUT", "timed out")))

    assert (result.outcome, result.retry_in_seconds) == (Outcome.FAILED, None)
    _, document = await load(session_factory, job)
    assert (document.status, document.error_code) == (DocumentStatus.FAILED, "LLM_TIMEOUT")


async def test_permanent_failure_is_final_with_its_code(session_factory: Factory) -> None:
    job = await new_job(session_factory, max_attempts=5)

    result = await run(
        session_factory,
        job,
        raising(PermanentError("ENCRYPTED_PDF", "The PDF is password-protected.")),
    )

    assert result.outcome is Outcome.FAILED
    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.attempts) == (JobStatus.FAILED, 1)  # no retry
    assert (document.error_code, document.error_message) == (
        "ENCRYPTED_PDF",
        "The PDF is password-protected.",
    )


async def test_time_limit_is_retried(session_factory: Factory) -> None:
    job = await new_job(session_factory)

    result = await run(session_factory, job, raising(SoftTimeLimitExceeded()))

    assert (result.outcome, result.error_code) == (Outcome.RETRY, TIME_LIMIT_EXCEEDED)


async def test_unexpected_errors_are_retried_logged_once_and_never_stored(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    secret = "CONFIDENTIAL: Q3 salary table row 17"

    with structlog.testing.capture_logs() as logs:
        result = await run(session_factory, job, raising(KeyError(secret)))

    assert (result.outcome, result.error_code) == (Outcome.RETRY, INTERNAL_ERROR)
    fresh, document = await load(session_factory, job)
    assert fresh.error_code == INTERNAL_ERROR
    assert secret not in (fresh.error_message or "")
    assert secret not in (document.error_message or "")
    errors = [e for e in logs if e["event"] == "job.unexpected_error"]
    assert len(errors) == 1
    assert errors[0]["log_level"] == "error"


async def test_unclaimable_job_is_skipped_and_the_work_never_runs(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    async with session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job.id)
            .values(status=JobStatus.SUCCEEDED)
        )
        await session.commit()
    ran = False

    async def work(run: JobRun) -> None:
        nonlocal ran
        ran = True

    result = await run(session_factory, job, work)

    assert (result.outcome, ran) == (Outcome.SKIPPED, False)


async def test_a_job_taken_back_mid_work_stops_and_writes_nothing(
    session_factory: Factory,
) -> None:
    job = await new_job(session_factory)
    reached_after_heartbeat = False

    async def work(run: JobRun) -> None:
        nonlocal reached_after_heartbeat
        # Meanwhile the job is cancelled (or its lease expired and someone else took it).
        async with session_factory() as session:
            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job.id)
                .values(status=JobStatus.CANCELLED)
            )
            await session.commit()
        await run.heartbeat(JobStage.EMBEDDING)
        reached_after_heartbeat = True

    with structlog.testing.capture_logs() as logs:
        result = await run(session_factory, job, work)

    assert result.outcome is Outcome.LOST
    assert reached_after_heartbeat is False
    # A lost lease is normal operation, not an error: one warning, no traceback/alert.
    events = [e["event"] for e in logs]
    assert "job.lost_during_work" in events
    assert "job.unexpected_error" not in events
    fresh, _ = await load(session_factory, job)
    assert (fresh.status, fresh.error_code, fresh.stage) == (JobStatus.CANCELLED, None, None)

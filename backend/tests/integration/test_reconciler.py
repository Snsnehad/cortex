"""The reconciler and job enqueueing (TASK-05.04).

The reconcile routine is called directly (no beat or worker containers): it gets the same
settings and session factory a worker process would.
"""

import asyncio
import contextlib
import json
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
import redis
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import Document, DocumentStatus, JobStatus, JobType, ProcessingJob
from app.repositories.jobs import JobRepository
from app.services.jobs import JobService
from app.workers.base import run_job
from app.workers.queue import INGEST_TASK, CeleryJobQueue, create_producer
from app.workers.runtime import WorkerContext
from app.workers.tasks import PROCESSING_NOT_AVAILABLE, ReconcileReport, _ingest, reconcile
from tests.integration.api_helpers import RecordingJobQueue, team
from tests.integration.db_helpers import add_all_and_commit
from tests.integration.factories import create_document, create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
ZERO = ReconcileReport(0, 0, 0, 0)


@pytest.fixture
def context(test_settings: Settings, session_factory: Factory) -> WorkerContext:
    # reconcile() only uses settings and the session factory of the worker context.
    return cast(
        WorkerContext, SimpleNamespace(settings=test_settings, session_factory=session_factory)
    )


async def new_job(factory: Factory, **values: Any) -> ProcessingJob:
    t = await team(factory)
    document = await create_document(factory, await create_kb(factory, t["org"]))
    job = ProcessingJob(
        organization_id=document.organization_id,
        document_id=document.id,
        job_type=JobType.INGEST,
        status=JobStatus.QUEUED,
        max_attempts=3,
    )
    await add_all_and_commit(factory, job)
    if values:
        await set_job(factory, job, **values)
    return job


async def set_job(factory: Factory, job: ProcessingJob, **values: Any) -> None:
    async with factory() as session:
        await session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job.id).values(**values)
        )
        await session.commit()


def ago(seconds: int) -> Any:
    return func.now() - timedelta(seconds=seconds)


async def load(factory: Factory, job: ProcessingJob) -> tuple[ProcessingJob, Document]:
    async with factory() as session:
        fresh = await session.get(ProcessingJob, job.id)
        document = await session.get(Document, job.document_id)
        assert fresh is not None
        assert document is not None
        return fresh, document


# --- lost messages --------------------------------------------------------------------------


async def test_a_queued_job_whose_message_was_lost_is_enqueued_once(
    context: WorkerContext, session_factory: Factory
) -> None:
    fresh = await new_job(session_factory)
    lost = await new_job(session_factory, updated_at=ago(11 * 60))  # > 10 min idle
    queue = RecordingJobQueue()

    first = await reconcile(context, queue)
    second = await reconcile(context, queue)

    assert queue.enqueued == [lost.id]  # not the fresh one; not twice
    assert (first.reenqueued, second) == (1, ZERO)
    assert (await load(session_factory, fresh))[0].status is JobStatus.QUEUED


async def test_a_retrying_job_is_enqueued_only_after_the_longest_backoff(
    context: WorkerContext, session_factory: Factory
) -> None:
    waiting = await new_job(
        session_factory, status=JobStatus.RETRYING, attempts=1, updated_at=ago(5 * 60)
    )
    idle = await new_job(
        session_factory, status=JobStatus.RETRYING, attempts=1, updated_at=ago(16 * 60)
    )
    queue = RecordingJobQueue()

    await reconcile(context, queue)

    assert queue.enqueued == [idle.id]
    assert waiting.id not in queue.enqueued


# --- crashed workers ------------------------------------------------------------------------


async def test_an_expired_lease_is_retried_at_once(
    context: WorkerContext, session_factory: Factory
) -> None:
    job = await new_job(session_factory)
    async with session_factory() as session:
        claim = await JobService(session, lease_seconds=300).claim(job.id)
    assert claim is not None
    await set_job(session_factory, job, lease_expires_at=ago(1))  # the worker died
    queue = RecordingJobQueue()

    report = await reconcile(context, queue)

    assert (report.recovered_for_retry, report.reenqueued) == (1, 1)
    assert queue.enqueued == [job.id]
    fresh, document = await load(session_factory, job)
    assert (fresh.status, fresh.error_code, fresh.lease_expires_at) == (
        JobStatus.RETRYING,
        "LEASE_EXPIRED",
        None,
    )
    assert document.status is DocumentStatus.PROCESSING  # will be retried
    # The crashed worker, if it was only stalled, can no longer write its result.
    async with session_factory() as session:
        assert await JobService(session, lease_seconds=300).succeed(claim) is False


async def test_an_expired_lease_on_the_last_attempt_fails_the_document(
    context: WorkerContext, session_factory: Factory
) -> None:
    job = await new_job(
        session_factory,
        status=JobStatus.RUNNING,
        attempts=3,
        lease_expires_at=ago(1),
        started_at=ago(400),
    )
    queue = RecordingJobQueue()

    report = await reconcile(context, queue)

    assert (report.failed_lease_expired, queue.enqueued) == (1, [])
    fresh, document = await load(session_factory, job)
    assert fresh.status is JobStatus.FAILED
    assert fresh.finished_at is not None
    assert (document.status, document.error_code) == (DocumentStatus.FAILED, "LEASE_EXPIRED")


async def test_healthy_and_finished_jobs_are_left_alone(
    context: WorkerContext, session_factory: Factory
) -> None:
    running = await new_job(
        session_factory,
        status=JobStatus.RUNNING,
        attempts=1,
        lease_expires_at=func.now() + timedelta(minutes=4),
    )
    final = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
    finished = [
        (await new_job(session_factory, status=status, attempts=1, updated_at=ago(3600)), status)
        for status in final
    ]
    queue = RecordingJobQueue()

    assert await reconcile(context, queue) == ZERO
    assert queue.enqueued == []
    assert (await load(session_factory, running))[0].status is JobStatus.RUNNING
    for job, status in finished:
        assert (await load(session_factory, job))[0].status is status


async def test_a_second_reconciler_skips_jobs_the_first_is_handling(
    context: WorkerContext, session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic overlap (lesson from TASK-03.06: gather alone may not overlap at the
    critical point). Reconciler A has selected and stamped the stale jobs but not yet
    committed (it holds their row locks); reconciler B runs meanwhile. B must skip those
    jobs (SKIP LOCKED); without the locking clause it would wait for A and then enqueue the
    same jobs a second time."""
    jobs = [await new_job(session_factory, updated_at=ago(3600)) for _ in range(3)]
    a_holds_locks = asyncio.Event()
    b_finished = asyncio.Event()
    original = JobRepository.touch_stale
    calls = 0

    async def touch_then_hold(self: JobRepository, **kwargs: Any) -> list[ProcessingJob]:
        nonlocal calls
        calls += 1
        rows = await original(self, **kwargs)
        if calls == 1:  # reconciler A, inside its transaction
            a_holds_locks.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(b_finished.wait(), timeout=2)
        return rows

    monkeypatch.setattr(JobRepository, "touch_stale", touch_then_hold)
    queue_a, queue_b = RecordingJobQueue(), RecordingJobQueue()

    async def reconciler_b() -> None:
        await a_holds_locks.wait()
        await reconcile(context, queue_b)
        b_finished.set()

    await asyncio.gather(reconcile(context, queue_a), reconciler_b())

    assert sorted(map(str, queue_a.enqueued)) == sorted(str(j.id) for j in jobs)
    assert queue_b.enqueued == []


async def test_a_broker_outage_is_counted_and_retried_on_a_later_run(
    context: WorkerContext, session_factory: Factory
) -> None:
    job = await new_job(session_factory, updated_at=ago(3600))

    report = await reconcile(context, RecordingJobQueue(available=False))

    assert (report.reenqueued, report.enqueue_failures) == (0, 1)
    assert (await load(session_factory, job))[0].status is JobStatus.QUEUED  # still waiting


# --- the ingest task (placeholder until Phase 6) --------------------------------------------


async def test_ingest_says_plainly_that_processing_is_not_available_yet(
    test_settings: Settings, session_factory: Factory
) -> None:
    job = await new_job(session_factory)

    result = await run_job(job.id, _ingest, factory=session_factory, settings=test_settings.worker)

    assert result.error_code == PROCESSING_NOT_AVAILABLE
    fresh, document = await load(session_factory, job)
    assert (fresh.status, document.status, document.error_code) == (
        JobStatus.FAILED,
        DocumentStatus.FAILED,
        PROCESSING_NOT_AVAILABLE,
    )


# --- the real broker ------------------------------------------------------------------------


def test_celery_queue_publishes_one_json_message(test_settings: Settings) -> None:
    producer = create_producer(test_settings)
    queue_name = f"cortex-test-{uuid.uuid4().hex}"
    producer.conf.task_default_queue = queue_name
    broker = redis.Redis.from_url(test_settings.worker.broker_url.get_secret_value())
    job_id = uuid.uuid4()
    try:
        assert CeleryJobQueue(producer).enqueue_ingest(job_id) is True
        raw = broker.lrange(queue_name, 0, -1)
        assert len(raw) == 1
        message = json.loads(raw[0])
        assert message["headers"]["task"] == INGEST_TASK
        assert message["content-type"] == "application/json"
        assert str(job_id) in json.dumps(message["headers"]["argsrepr"])
    finally:
        # The queue and Celery's routing keys for it: nothing may pile up in Redis.
        for key in broker.scan_iter(match=f"*{queue_name}*"):
            broker.delete(key)
        assert not list(broker.scan_iter(match=f"*{queue_name}*"))
        broker.close()
        producer.close()


def test_broker_down_is_reported_quickly_not_raised(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={
            "worker": test_settings.worker.model_copy(
                update={
                    "broker_url": test_settings.worker.broker_url.__class__("redis://127.0.0.1:1/1")
                }
            )
        }
    )
    producer = create_producer(settings)
    try:
        started = time.monotonic()
        assert CeleryJobQueue(producer).enqueue_ingest(uuid.uuid4()) is False
        assert time.monotonic() - started < 10  # bounded by the enqueue timeout
    finally:
        producer.close()


def test_a_job_type_without_a_task_is_not_enqueued(test_settings: Settings) -> None:
    producer = create_producer(test_settings)
    try:
        assert CeleryJobQueue(producer).enqueue(uuid.uuid4(), JobType.REINDEX) is False
    finally:
        producer.close()

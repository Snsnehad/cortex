"""The delete task: file then rows, idempotent, fenced; KB row removal (TASK-05.05).

Runs the task's work directly through run_job (no worker container), with a local storage
root in the test's temp directory.
"""

import asyncio
import contextlib
import hashlib
import io
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.tenancy import OrgContext
from app.models import (
    Document,
    DocumentStatus,
    JobStatus,
    JobType,
    KnowledgeBase,
    OrgRole,
    ProcessingJob,
)
from app.repositories.jobs import JobRepository
from app.services.documents import DocumentNotFoundError, DocumentService
from app.services.jobs import JobService
from app.services.knowledge_bases import KnowledgeBaseService
from app.storage import StorageUnavailableError
from app.storage.local import LocalStorage
from app.uploads import DetectedType, ReceivedFile
from app.workers.base import Outcome, run_job
from app.workers.runtime import WorkerContext
from app.workers.tasks import _delete_document, reconcile
from tests.integration.api_helpers import RecordingJobQueue, team
from tests.integration.factories import create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
async def world(session_factory: Factory) -> dict[str, Any]:
    t = await team(session_factory)
    t["kb"] = await create_kb(session_factory, t["org"], name="Handbook")
    t["ctx"] = OrgContext(organization_id=t["org"].id, user=t["owner"], role=OrgRole.OWNER)
    return t


async def add_document(
    factory: Factory, storage: LocalStorage, world: dict[str, Any], content: bytes
) -> Document:
    upload = ReceivedFile(
        filename="a.txt",
        declared_content_type="text/plain",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        file=io.BytesIO(content),
    )
    async with factory() as session:
        added = await DocumentService(session, storage).add(
            world["ctx"], world["kb"].id, upload, DetectedType("text/plain", "text")
        )
    return added.document


async def request_delete(
    factory: Factory, storage: LocalStorage, world: dict[str, Any], document: Document
) -> uuid.UUID:
    async with factory() as session:
        deleted = await DocumentService(session, storage).delete(world["ctx"], document.id)
    return deleted.job.id


async def run_delete(factory: Factory, storage: Any, job_id: uuid.UUID, settings: Settings) -> Any:
    return await run_job(
        job_id, _delete_document, factory=factory, settings=settings.worker, storage=storage
    )


async def count(factory: Factory, model: Any, *where: Any) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_delete_removes_the_file_then_the_rows(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    document = await add_document(session_factory, storage, world, b"to be deleted\n")
    path = storage.path_for(document.storage_key)
    job_id = await request_delete(session_factory, storage, world, document)

    result = await run_delete(session_factory, storage, job_id, test_settings)

    assert result.outcome is Outcome.SUCCEEDED
    assert not path.exists()
    assert await count(session_factory, Document, Document.id == document.id) == 0
    assert (
        await count(session_factory, ProcessingJob, ProcessingJob.document_id == document.id) == 0
    )


async def test_running_it_again_finds_nothing_to_do(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    document = await add_document(session_factory, storage, world, b"twice\n")
    job_id = await request_delete(session_factory, storage, world, document)
    await run_delete(session_factory, storage, job_id, test_settings)

    again = await run_delete(session_factory, storage, job_id, test_settings)

    assert again.outcome is Outcome.SKIPPED


async def test_a_file_already_gone_is_fine(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    """A retry after a crash between deleting the file and deleting the rows."""
    document = await add_document(session_factory, storage, world, b"half done\n")
    storage.path_for(document.storage_key).unlink()
    job_id = await request_delete(session_factory, storage, world, document)

    result = await run_delete(session_factory, storage, job_id, test_settings)

    assert result.outcome is Outcome.SUCCEEDED
    assert await count(session_factory, Document, Document.id == document.id) == 0


async def test_storage_outage_is_retried_and_keeps_the_rows(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    document = await add_document(session_factory, storage, world, b"keep for now\n")
    job_id = await request_delete(session_factory, storage, world, document)

    class Unavailable:
        async def delete(self, key: str) -> None:
            raise StorageUnavailableError

    result = await run_delete(session_factory, Unavailable(), job_id, test_settings)

    assert (result.outcome, result.error_code) == (Outcome.RETRY, "STORAGE_UNAVAILABLE")
    async with session_factory() as session:
        row = await session.get(Document, document.id)
    assert row is not None
    assert row.status is DocumentStatus.DELETING  # still invisible, retried later
    assert storage.path_for(document.storage_key).exists()


async def test_a_stale_delete_worker_removes_nothing(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    document = await add_document(session_factory, storage, world, b"fenced\n")
    job_id = await request_delete(session_factory, storage, world, document)

    async def taken_over_then_work(run: Any) -> None:
        # The lease expired and another worker claimed the job (attempt 2) meanwhile.
        async with session_factory() as session:
            await session.execute(
                update(ProcessingJob).where(ProcessingJob.id == job_id).values(attempts=2)
            )
            await session.commit()
        await _delete_document(run)

    result = await run_job(
        job_id,
        taken_over_then_work,
        factory=session_factory,
        settings=test_settings.worker,
        storage=storage,
    )

    assert result.outcome is Outcome.LOST
    assert await count(session_factory, Document, Document.id == document.id) == 1


async def test_an_ingest_worker_is_fenced_out_by_a_delete(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    document = await add_document(session_factory, storage, world, b"busy\n")
    async with session_factory() as session:
        ingest_job = await session.scalar(
            select(ProcessingJob.id).where(ProcessingJob.document_id == document.id)
        )
        assert ingest_job is not None
        claim = await JobService(session, lease_seconds=300).claim(ingest_job)
    assert claim is not None

    await request_delete(session_factory, storage, world, document)

    async with session_factory() as session:
        assert await JobService(session, lease_seconds=300).succeed(claim) is False
        row = await session.get(Document, document.id)
    assert row is not None
    assert row.status is DocumentStatus.DELETING  # not overwritten with READY


async def test_the_last_document_takes_its_deleted_kb_with_it(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any], test_settings: Settings
) -> None:
    documents = [
        await add_document(session_factory, storage, world, f"{i}\n".encode()) for i in range(2)
    ]
    async with session_factory() as session:
        deleted = await KnowledgeBaseService(session, test_settings).delete(
            world["ctx"], world["kb"].id
        )

    first = await run_delete(session_factory, storage, deleted.delete_job_ids[0], test_settings)
    assert first.outcome is Outcome.SUCCEEDED
    assert await count(session_factory, KnowledgeBase, KnowledgeBase.id == world["kb"].id) == 1

    await run_delete(session_factory, storage, deleted.delete_job_ids[1], test_settings)

    assert await count(session_factory, KnowledgeBase, KnowledgeBase.id == world["kb"].id) == 0
    assert all(not storage.path_for(d.storage_key).exists() for d in documents)


async def test_the_reconciler_removes_empty_deleted_kbs_only(
    session_factory: Factory, world: dict[str, Any], test_settings: Settings
) -> None:
    empty_deleted = await create_kb(session_factory, world["org"], name="Empty, deleted")
    live = await create_kb(session_factory, world["org"], name="Live")
    async with session_factory() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id.in_([empty_deleted.id]))
            .values(deleted_at=func.now())
        )
        await session.commit()
    context = cast(
        WorkerContext, SimpleNamespace(settings=test_settings, session_factory=session_factory)
    )

    report = await reconcile(context, RecordingJobQueue())

    assert report.knowledge_bases_removed == 1
    assert await count(session_factory, KnowledgeBase, KnowledgeBase.id == empty_deleted.id) == 0
    assert (
        await count(session_factory, KnowledgeBase, KnowledgeBase.id.in_([live.id, world["kb"].id]))
        == 2
    )


async def test_a_delete_job_can_be_claimed_for_a_deleting_document(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    document = await add_document(session_factory, storage, world, b"claim me\n")
    job_id = await request_delete(session_factory, storage, world, document)

    async with session_factory() as session:
        claim = await JobService(session, lease_seconds=300).claim(job_id)

    assert claim is not None
    assert claim.job_type is JobType.DELETE


async def test_a_reprocess_cannot_slip_into_a_delete(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic interleaving (lesson from 03.06): the delete has cancelled the active
    jobs and is about to queue its DELETE job; a reprocess runs at exactly that moment.
    The document row lock makes the reprocess wait and then see DELETING (404). Without it,
    the reprocess would commit a new INGEST job and the delete would collide with it on the
    one-active-job index and fail."""
    document = await add_document(session_factory, storage, world, b"race\n")
    async with session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.document_id == document.id)
            .values(status=JobStatus.FAILED)
        )
        await session.commit()
    delete_paused = asyncio.Event()
    reprocess_done = asyncio.Event()
    original = JobRepository.cancel_active_for_documents

    async def cancel_then_pause(self: JobRepository, *args: Any) -> int:
        cancelled = await original(self, *args)
        delete_paused.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(reprocess_done.wait(), timeout=1)
        return cancelled

    monkeypatch.setattr(JobRepository, "cancel_active_for_documents", cancel_then_pause)
    outcomes: dict[str, str] = {}

    async def delete() -> None:
        async with session_factory() as session:
            await DocumentService(session, storage).delete(world["ctx"], document.id)
        outcomes["delete"] = "202"

    async def reprocess() -> None:
        # Bounded: if the delete never reaches its pause (e.g. its cancel step is missing),
        # the test must fail on its assertions, not hang.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(delete_paused.wait(), timeout=2)
        try:
            async with session_factory() as session:
                await DocumentService(session, storage).reprocess(world["ctx"], document.id)
            outcomes["reprocess"] = "202"
        except DocumentNotFoundError:
            outcomes["reprocess"] = "404"
        finally:
            reprocess_done.set()

    await asyncio.gather(delete(), reprocess())

    assert outcomes == {"delete": "202", "reprocess": "404"}

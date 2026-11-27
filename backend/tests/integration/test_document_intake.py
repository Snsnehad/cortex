"""Adding documents: per-KB de-duplication, the storage/database handshake, cleanup
(TASK-04.04). Runs against the real database and a local storage root in a temp dir."""

import asyncio
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any

import anyio
import pytest
import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import OrgContext
from app.models import Document, DocumentStatus, KnowledgeBase, OrgRole
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.services.documents import (
    DocumentDuplicateError,
    DocumentService,
    StoredContentMismatchError,
)
from app.services.knowledge_bases import KnowledgeBaseNotFoundError
from app.storage import StoredObject
from app.storage.local import LocalStorage
from app.uploads import DetectedType, ReceivedFile
from tests.integration.api_helpers import team
from tests.integration.factories import create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
TEXT = DetectedType("text/plain", "text")
CONTENT = b"Quarterly report: revenue grew.\n" * 50


def upload(content: bytes = CONTENT, filename: str = "report.txt") -> ReceivedFile:
    return ReceivedFile(
        filename=filename,
        declared_content_type="text/plain",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        file=io.BytesIO(content),
    )


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def stored_files(storage: LocalStorage) -> list[Path]:
    return sorted(p for p in storage.root.rglob("*") if p.is_file())


@pytest.fixture
async def world(session_factory: Factory) -> dict[str, Any]:
    t = await team(session_factory)
    t["kb"] = await create_kb(session_factory, t["org"], name="Reports")
    t["ctx"] = OrgContext(organization_id=t["org"].id, user=t["member"], role=OrgRole.MEMBER)
    return t


async def add(
    factory: Factory, storage: LocalStorage, world: dict[str, Any], **kwargs: Any
) -> Document:
    async with factory() as session:
        service = DocumentService(session, storage)
        added = await service.add(
            kwargs.get("ctx", world["ctx"]),
            kwargs.get("kb_id", world["kb"].id),
            kwargs.get("upload", upload()),
            TEXT,
        )
        return added.document


async def documents(factory: Factory) -> list[Document]:
    async with factory() as session:
        return list((await session.scalars(select(Document))).all())


# --- the normal path ------------------------------------------------------------------------


async def test_add_stores_the_file_and_records_a_pending_document(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    document = await add(session_factory, storage, world)

    assert document.status is DocumentStatus.PENDING
    assert (document.filename, document.mime_type) == ("report.txt", "text/plain")
    assert document.file_size_bytes == len(CONTENT)
    assert document.content_hash == hashlib.sha256(CONTENT).hexdigest()
    assert document.uploaded_by_user_id == world["member"].id
    assert document.storage_key == (
        f"orgs/{world['org'].id}/kbs/{world['kb'].id}/docs/{document.id}/original"
    )
    assert storage.path_for(document.storage_key).read_bytes() == CONTENT
    assert [d.id for d in await documents(session_factory)] == [document.id]


# --- duplicates -----------------------------------------------------------------------------


async def test_duplicate_is_refused_before_anything_is_stored(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await add(session_factory, storage, world)
    interfering = Interfering(storage, monkeypatch)  # counts put() calls from here on

    with pytest.raises(DocumentDuplicateError) as caught:
        await add(session_factory, storage, world, upload=upload(filename="copy of report.txt"))

    assert caught.value.code == "DOCUMENT_DUPLICATE"
    assert caught.value.details == {"existing_document_id": str(first.id)}
    assert interfering.calls == 0  # the pre-check answered: nothing was written
    assert len(stored_files(storage)) == 1
    assert len(await documents(session_factory)) == 1


async def test_same_file_is_fine_in_another_kb_or_org(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    other_kb = await create_kb(session_factory, world["org"], name="Archive")
    other = await team(session_factory)
    other_org_kb = await create_kb(session_factory, other["org"])
    other_ctx = OrgContext(
        organization_id=other["org"].id, user=other["member"], role=OrgRole.MEMBER
    )

    await add(session_factory, storage, world)
    await add(session_factory, storage, world, kb_id=other_kb.id)
    await add(session_factory, storage, world, ctx=other_ctx, kb_id=other_org_kb.id)

    assert len(await documents(session_factory)) == 3
    assert len(stored_files(storage)) == 3


async def test_a_document_being_deleted_does_not_block_a_reupload(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    old = await add(session_factory, storage, world)
    async with session_factory() as session:
        await session.execute(
            update(Document).where(Document.id == old.id).values(status=DocumentStatus.DELETING)
        )
        await session.commit()

    new = await add(session_factory, storage, world)

    assert new.id != old.id


@pytest.fixture
def both_pass_the_pre_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold each upload right after its duplicate pre-check until both have done it, so the
    two are guaranteed to race at the unique index (asyncio.gather alone may not overlap)."""
    barrier = asyncio.Barrier(2)
    original = DocumentRepository.find_active_by_hash
    waited: set[int] = set()

    async def find_then_wait(self: DocumentRepository, *args: Any) -> Document | None:
        result = await original(self, *args)
        if id(self) not in waited:  # only the pre-check, not the winner lookup
            waited.add(id(self))
            async with asyncio.timeout(5):
                await barrier.wait()
        return result

    monkeypatch.setattr(DocumentRepository, "find_active_by_hash", find_then_wait)


@pytest.mark.usefixtures("both_pass_the_pre_check")
async def test_two_identical_uploads_at_once_make_exactly_one_document(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    results = await asyncio.gather(
        add(session_factory, storage, world),
        add(session_factory, storage, world),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, Document)]
    losers = [r for r in results if isinstance(r, DocumentDuplicateError)]
    assert (len(winners), len(losers)) == (1, 1), results
    assert losers[0].existing_document_id == winners[0].id
    assert [d.id for d in await documents(session_factory)] == [winners[0].id]
    # The loser's object was removed: only the winner's file is in storage.
    assert stored_files(storage) == [storage.path_for(winners[0].storage_key)]


# --- the knowledge base ---------------------------------------------------------------------


async def test_unknown_or_deleted_kb_stores_nothing(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == world["kb"].id)
            .values(deleted_at=func.now())
        )
        await session.commit()

    for kb_id in (world["kb"].id, uuid.uuid4()):
        with pytest.raises(KnowledgeBaseNotFoundError):
            await add(session_factory, storage, world, kb_id=kb_id)
    assert stored_files(storage) == []


async def test_another_orgs_kb_is_not_found(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    other = await team(session_factory)
    foreign_kb = await create_kb(session_factory, other["org"])

    with pytest.raises(KnowledgeBaseNotFoundError):
        await add(session_factory, storage, world, kb_id=foreign_kb.id)
    assert stored_files(storage) == []


# --- failures after the file was stored -----------------------------------------------------


class Interfering:
    """Wraps LocalStorage.put to do something between storing and inserting."""

    def __init__(self, storage: LocalStorage, monkeypatch: pytest.MonkeyPatch) -> None:
        self.original = storage.put
        self.storage = storage
        monkeypatch.setattr(storage, "put", self.put)
        self.after_put: Any = None
        self.result_override: StoredObject | None = None
        self.calls = 0

    async def put(self, key: str, data: Any, **kwargs: Any) -> StoredObject:
        self.calls += 1
        stored = await self.original(key, data, **kwargs)
        if self.after_put:
            await self.after_put()
        return self.result_override or stored


async def test_kb_deleted_while_storing_removes_the_object(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interfering = Interfering(storage, monkeypatch)

    async def delete_kb() -> None:
        async with session_factory() as session:
            await session.execute(
                update(KnowledgeBase)
                .where(KnowledgeBase.id == world["kb"].id)
                .values(deleted_at=func.now())
            )
            await session.commit()

    interfering.after_put = delete_kb

    with pytest.raises(KnowledgeBaseNotFoundError):
        await add(session_factory, storage, world)
    assert stored_files(storage) == []
    assert await documents(session_factory) == []


async def test_database_error_after_storing_removes_the_object(
    session_factory: Factory, storage: LocalStorage, world: dict[str, Any]
) -> None:
    too_long = upload(filename="x" * 300)  # violates varchar(255) at INSERT

    with pytest.raises(Exception, match="value too long"):
        await add(session_factory, storage, world, upload=too_long)
    assert stored_files(storage) == []


async def test_content_mismatch_is_caught_and_the_object_removed(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interfering = Interfering(storage, monkeypatch)
    interfering.result_override = StoredObject(key="x", size_bytes=len(CONTENT), sha256="0" * 64)

    with pytest.raises(StoredContentMismatchError):
        await add(session_factory, storage, world)
    assert stored_files(storage) == []
    assert await documents(session_factory) == []


async def test_cancelled_request_still_removes_the_object(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client disconnects after the file is stored: cleanup must survive cancellation."""
    interfering = Interfering(storage, monkeypatch)
    stored = anyio.Event()

    async def hang() -> None:
        stored.set()
        await anyio.sleep_forever()

    interfering.after_put = hang

    async with anyio.create_task_group() as group:
        group.start_soon(add, session_factory, storage, world)
        await stored.wait()
        group.cancel_scope.cancel()

    assert stored_files(storage) == []


async def test_failed_cleanup_is_logged_and_the_original_error_kept(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_delete(key: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(storage, "delete", broken_delete)

    with structlog.testing.capture_logs() as logs, pytest.raises(Exception, match="too long"):
        await add(session_factory, storage, world, upload=upload(filename="x" * 300))

    orphan = [e for e in logs if e["event"] == "document.cleanup_failed"]
    assert len(orphan) == 1
    assert orphan[0]["storage_key"].startswith(f"orgs/{world['org'].id}/kbs/")
    assert "x" * 300 not in str(logs)  # the filename (personal data) is never logged


async def test_a_kb_delete_waits_for_an_insert_in_progress(
    session_factory: Factory,
    storage: LocalStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While a document is being inserted, the knowledge base is locked FOR SHARE: a soft
    delete must wait. Otherwise the delete could commit first and a cleanup job starting
    right after it would miss the document committed a moment later."""
    original = KnowledgeBaseRepository.get_active
    outcome: dict[str, str] = {}

    async def get_then_try_to_delete(
        self: KnowledgeBaseRepository, org_id: Any, kb_id: Any, *, for_share: bool = False
    ) -> KnowledgeBase | None:
        result = await original(self, org_id, kb_id, for_share=for_share)
        if for_share:  # inside the insert transaction, right after taking the lock
            async with session_factory() as other:
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                try:
                    await other.execute(
                        update(KnowledgeBase)
                        .where(KnowledgeBase.id == kb_id)
                        .values(deleted_at=func.now())
                    )
                    await other.commit()
                    outcome["delete"] = "committed"
                except DBAPIError:
                    outcome["delete"] = "blocked"
        return result

    monkeypatch.setattr(KnowledgeBaseRepository, "get_active", get_then_try_to_delete)

    document = await add(session_factory, storage, world)

    assert outcome == {"delete": "blocked"}
    assert document.status is DocumentStatus.PENDING

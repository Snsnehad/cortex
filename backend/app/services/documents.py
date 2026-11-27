"""Adding documents to a knowledge base: de-duplication, storage, the document row
(TASK-04.04) and its first processing job (TASK-04.05).

The same bytes may live in a knowledge base only once (documents being deleted do not
count). ``add`` runs in three steps, and no database transaction stays open while the file
travels to storage:

1. **Pre-check**: is there already a live document with this SHA-256 in this knowledge base?
   Then 409 ``DOCUMENT_DUPLICATE`` with ``details.existing_document_id``, and **nothing is
   stored**. This is the common case and costs one indexed lookup.
2. **Store** the file under a key built from ids (``orgs/…/kbs/…/docs/<new id>/original``).
3. **Insert** the document (``PENDING``) **and** its ``INGEST`` job (``QUEUED``) in one short
   transaction that first locks the knowledge base ``FOR SHARE``, so it cannot be
   soft-deleted halfway. Both rows exist or neither does: a worker never sees a document
   without a job, or a job without a document. Until Celery arrives (Phase 5) the job simply
   waits in ``QUEUED``; the reconciler enqueues jobs it finds there.

Two identical uploads at the same moment both pass step 1. The partial unique index
``uq_documents_kb_content_hash_active`` lets exactly one insert succeed. The loser deletes
the object it just stored and answers 409 with the **winner's** id, so no orphan file is
left behind. Any other failure after step 2 (knowledge base deleted meanwhile, database
error, cancelled request, even a cancellation that lands while ``put`` is finishing)
deletes the object too; that cleanup is shielded from cancellation. If even the cleanup
fails, the key is logged (``document.cleanup_failed``) for the Phase 5 reconciler.
"""

import hashlib
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import IO

from anyio import CancelScope, to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.pagination import CursorCodec, PageResult
from app.core.tenancy import OrgContext
from app.models import Document, DocumentStatus, JobStatus, JobType, ProcessingJob
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.repositories.pagination import KeysetOrder, PageRequest, fetch_page
from app.services.knowledge_bases import KnowledgeBaseNotFoundError
from app.storage import StorageBackend, document_key
from app.uploads import DetectedType, ReceivedFile
from app.utils.ids import uuid7

logger = get_logger("cortex.documents")

DUPLICATE_INDEX = "uq_documents_kb_content_hash_active"
READ_CHUNK_BYTES = 1024 * 1024


class DocumentDuplicateError(ConflictError):
    default_code = "DOCUMENT_DUPLICATE"
    default_message = "This file already exists in the knowledge base."

    def __init__(self, existing_document_id: uuid.UUID) -> None:
        super().__init__(details={"existing_document_id": str(existing_document_id)})
        self.existing_document_id = existing_document_id


@dataclass(frozen=True, slots=True)
class AddedDocument:
    document: Document
    job: ProcessingJob


class DocumentNotFoundError(NotFoundError):
    default_code = "DOCUMENT_NOT_FOUND"
    default_message = "Document not found."


@dataclass(frozen=True, slots=True)
class DocumentWithJob:
    document: Document
    job: ProcessingJob | None  # the latest job; None only for data created outside the API


@dataclass(frozen=True, slots=True)
class Download:
    """Either a redirect to a direct (presigned) URL, or a stream the API serves itself."""

    document: Document
    redirect_url: str | None = None
    chunks: AsyncGenerator[bytes] | None = None


NEWEST_FIRST = KeysetOrder(Document.created_at, Document.id, descending=True)


class StoredContentMismatchError(RuntimeError):
    """The bytes that reached storage are not the bytes that were received (a bug)."""


def _violated_constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


async def _read_chunks(file: IO[bytes]) -> AsyncIterator[bytes]:
    await to_thread.run_sync(file.seek, 0)
    while chunk := await to_thread.run_sync(file.read, READ_CHUNK_BYTES):
        yield chunk


class DocumentService:
    def __init__(self, session: AsyncSession, storage: StorageBackend) -> None:
        self._session = session
        self._storage = storage
        self._documents = DocumentRepository(session)
        self._knowledge_bases = KnowledgeBaseRepository(session)

    async def require_knowledge_base(self, ctx: OrgContext, knowledge_base_id: uuid.UUID) -> None:
        """404 now, before the client sends megabytes to a knowledge base that is not there
        (or belongs to another organization). ``add`` checks again; this is only early."""
        found = await self._knowledge_bases.get_active(ctx.organization_id, knowledge_base_id)
        # End the read-only transaction (no transaction stays open while the body streams
        # in). commit(), not rollback(): rollback expires every loaded object, including the
        # caller's User in ctx, and touching it later would need a lazy (sync) refresh.
        await self._session.commit()
        if found is None:
            raise KnowledgeBaseNotFoundError

    async def add(
        self,
        ctx: OrgContext,
        knowledge_base_id: uuid.UUID,
        upload: ReceivedFile,
        detected: DetectedType,
    ) -> AddedDocument:
        org_id = ctx.organization_id
        # 1. Pre-check, then end the read transaction before the slow storage write.
        if await self._knowledge_bases.get_active(org_id, knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError
        existing = await self._documents.find_active_by_hash(
            org_id, knowledge_base_id, upload.sha256
        )
        existing_id = existing.id if existing is not None else None
        await self._session.commit()  # end the read transaction; see require_knowledge_base
        if existing_id is not None:
            raise DocumentDuplicateError(existing_id)

        # 2. Store under an id-based key.
        document_id = uuid7()
        key = document_key(org_id, knowledge_base_id, document_id)
        try:
            # Inside the cleanup scope: a cancellation can arrive just as the worker thread
            # finishes writing, leaving the object stored although put() never returned.
            stored = await self._storage.put(
                key, _read_chunks(upload.file), content_type=detected.mime_type
            )
            if (stored.sha256, stored.size_bytes) != (upload.sha256, upload.size_bytes):
                raise StoredContentMismatchError(key)
            # 3. Insert, with the knowledge base locked against a concurrent delete.
            return await self._insert(ctx, knowledge_base_id, document_id, key, upload, detected)
        except BaseException:
            with CancelScope(shield=True):
                await self._discard(key)
            raise

    async def _insert(
        self,
        ctx: OrgContext,
        knowledge_base_id: uuid.UUID,
        document_id: uuid.UUID,
        key: str,
        upload: ReceivedFile,
        detected: DetectedType,
    ) -> AddedDocument:
        org_id = ctx.organization_id
        locked = await self._knowledge_bases.get_active(org_id, knowledge_base_id, for_share=True)
        if locked is None:  # deleted while the file was being stored
            await self._session.rollback()
            raise KnowledgeBaseNotFoundError
        document = Document(
            id=document_id,
            organization_id=org_id,
            knowledge_base_id=knowledge_base_id,
            uploaded_by_user_id=ctx.user_id,
            filename=upload.filename,
            mime_type=detected.mime_type,
            file_size_bytes=upload.size_bytes,
            content_hash=upload.sha256,
            storage_key=key,
            status=DocumentStatus.PENDING,
        )
        job = ProcessingJob(
            organization_id=org_id,
            document_id=document_id,
            job_type=JobType.INGEST,
            status=JobStatus.QUEUED,
        )
        self._documents.add(document)
        self._documents.add_job(job)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _violated_constraint(exc) != DUPLICATE_INDEX:
                raise
            # Lost the race to an identical upload: report the winner.
            winner = await self._documents.find_active_by_hash(
                org_id, knowledge_base_id, upload.sha256
            )
            winner_id = winner.id if winner is not None else None  # before rollback (see add)
            await self._session.rollback()
            logger.info(
                "document.duplicate_race_lost",
                knowledge_base_id=str(knowledge_base_id),
                winner_id=str(winner_id) if winner_id else None,
            )
            if winner_id is None:  # the winner was deleted in the meantime: extremely rare
                raise ConflictError(
                    "The same file was uploaded at the same moment; please retry.",
                    code="DOCUMENT_DUPLICATE",
                ) from exc
            raise DocumentDuplicateError(winner_id) from exc
        logger.info(
            "document.added",
            document_id=str(document_id),
            knowledge_base_id=str(knowledge_base_id),
            size_bytes=upload.size_bytes,
            mime_type=detected.mime_type,
            job_id=str(job.id),
        )
        return AddedDocument(document, job)

    async def _discard(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:  # noqa: BLE001 - cleanup must never hide the original error
            # The object may be left behind (or may never have been written); the Phase 5
            # reconciler removes objects that no document row refers to.
            logger.warning("document.cleanup_failed", storage_key=key)

    # --- reads (TASK-04.06) ---------------------------------------------------------------

    async def list_in_kb(
        self,
        ctx: OrgContext,
        knowledge_base_id: uuid.UUID,
        *,
        status: DocumentStatus | None,
        filename_contains: str | None,
        page: PageRequest,
        codec: CursorCodec,
    ) -> PageResult[Document]:
        if await self._knowledge_bases.get_active(ctx.organization_id, knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError
        # The cursor is bound to the filters. Cursors are readable (base64), so the search
        # text enters the scope only as a hash.
        q_ref = (
            hashlib.sha256(filename_contains.encode()).hexdigest()[:16] if filename_contains else ""
        )
        return await fetch_page(
            self._session,
            DocumentRepository.visible_in_kb_query(
                ctx.organization_id,
                knowledge_base_id,
                status=status,
                filename_contains=filename_contains,
            ),
            NEWEST_FIRST,
            page=page,
            scope=f"docs:kb={knowledge_base_id}:status={status or ''}:q={q_ref}:created:desc",
            codec=codec,
        )

    async def get(self, ctx: OrgContext, document_id: uuid.UUID) -> DocumentWithJob:
        document = await self._get_visible(ctx, document_id)
        job = await self._documents.latest_job(ctx.organization_id, document_id)
        return DocumentWithJob(document, job)

    async def download(
        self, ctx: OrgContext, document_id: uuid.UUID, *, link_ttl_seconds: int
    ) -> Download:
        document = await self._get_visible(ctx, document_id)
        # End the read transaction before talking to storage (see require_knowledge_base).
        await self._session.commit()
        url = await self._storage.presigned_get_url(
            document.storage_key, filename=document.filename, expires_in_seconds=link_ttl_seconds
        )
        if url is not None:
            return Download(document, redirect_url=url)
        return Download(
            document, chunks=await _started(self._storage.iter_chunks(document.storage_key))
        )

    async def _get_visible(self, ctx: OrgContext, document_id: uuid.UUID) -> Document:
        document = await self._documents.get_visible(ctx.organization_id, document_id)
        if document is None:
            raise DocumentNotFoundError
        return document


async def _started(chunks: AsyncGenerator[bytes]) -> AsyncGenerator[bytes]:
    """Pull the first chunk now, so a missing object fails *before* the response starts
    (afterwards the client would only see a truncated download)."""
    try:
        first = await anext(chunks, None)
    except BaseException:
        await chunks.aclose()
        raise

    async def stream() -> AsyncGenerator[bytes]:
        try:
            if first is not None:
                yield first
            async for chunk in chunks:
                yield chunk
        finally:
            await chunks.aclose()

    return stream()

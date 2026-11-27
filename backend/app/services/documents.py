"""Adding documents to a knowledge base: de-duplication, storage, the document row
(TASK-04.04; the processing job and the HTTP endpoint are TASK-04.05).

The same bytes may live in a knowledge base only once (documents being deleted do not
count). ``add`` runs in three steps, and no database transaction stays open while the file
travels to storage:

1. **Pre-check**: is there already a live document with this SHA-256 in this knowledge base?
   Then 409 ``DOCUMENT_DUPLICATE`` with ``details.existing_document_id``, and **nothing is
   stored**. This is the common case and costs one indexed lookup.
2. **Store** the file under a key built from ids (``orgs/…/kbs/…/docs/<new id>/original``).
3. **Insert** the document (``PENDING``) in a short transaction that first locks the
   knowledge base ``FOR SHARE``, so it cannot be soft-deleted halfway.

Two identical uploads at the same moment both pass step 1. The partial unique index
``uq_documents_kb_content_hash_active`` lets exactly one insert succeed. The loser deletes
the object it just stored and answers 409 with the **winner's** id, so no orphan file is
left behind. Any other failure after step 2 (knowledge base deleted meanwhile, database
error, cancelled request, even a cancellation that lands while ``put`` is finishing)
deletes the object too; that cleanup is shielded from cancellation. If even the cleanup
fails, the key is logged (``document.cleanup_failed``) for the Phase 5 reconciler.
"""

import uuid
from collections.abc import AsyncIterator
from typing import IO

from anyio import CancelScope, to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.tenancy import OrgContext
from app.models import Document, DocumentStatus
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
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

    async def add(
        self,
        ctx: OrgContext,
        knowledge_base_id: uuid.UUID,
        upload: ReceivedFile,
        detected: DetectedType,
    ) -> Document:
        org_id = ctx.organization_id
        # 1. Pre-check, then end the read transaction before the slow storage write.
        if await self._knowledge_bases.get_active(org_id, knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError
        existing = await self._documents.find_active_by_hash(
            org_id, knowledge_base_id, upload.sha256
        )
        # Read the id BEFORE rollback: rollback expires every loaded object, and touching an
        # expired attribute would trigger a lazy refresh (not allowed in async code).
        existing_id = existing.id if existing is not None else None
        await self._session.rollback()
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
    ) -> Document:
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
        self._documents.add(document)
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
        )
        return document

    async def _discard(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:  # noqa: BLE001 - cleanup must never hide the original error
            # The object may be left behind (or may never have been written); the Phase 5
            # reconciler removes objects that no document row refers to.
            logger.warning("document.cleanup_failed", storage_key=key)

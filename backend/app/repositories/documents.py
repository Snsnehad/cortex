"""Data access for documents. Repositories never commit.

Every lookup takes the organization, never a document id or a hash alone. "Visible" means
what users may see: not ``DELETING``, and in a knowledge base that is not deleted.
"""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, KnowledgeBase, ProcessingJob

LIKE_ESCAPE = "\\"


def escape_like(text: str) -> str:
    """Make ``%``, ``_`` and the escape character itself match literally in LIKE/ILIKE."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, document: Document) -> None:
        self._session.add(document)

    def add_job(self, job: ProcessingJob) -> None:
        self._session.add(job)

    async def find_active_by_hash(
        self, organization_id: uuid.UUID, knowledge_base_id: uuid.UUID, content_hash: str
    ) -> Document | None:
        """The live document in this knowledge base with these exact bytes, if any.

        Mirrors the unique index ``uq_documents_kb_content_hash_active``: documents being
        deleted do not count, so a file can be uploaded again while its old copy is cleaned up.
        """
        document: Document | None = await self._session.scalar(
            select(Document).where(
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.content_hash == content_hash,
                Document.status != DocumentStatus.DELETING,
            )
        )
        return document

    @staticmethod
    def visible_in_kb_query(
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
        *,
        status: DocumentStatus | None = None,
        filename_contains: str | None = None,
    ) -> Select[Any]:
        """A knowledge base's visible documents (the caller has checked the KB is live)."""
        query = select(Document).where(
            Document.organization_id == organization_id,
            Document.knowledge_base_id == knowledge_base_id,
            Document.status != DocumentStatus.DELETING,
        )
        if status is not None:
            query = query.where(Document.status == status)
        if filename_contains:
            pattern = f"%{escape_like(filename_contains)}%"
            query = query.where(Document.filename.ilike(pattern, escape=LIKE_ESCAPE))
        return query

    async def get_visible(
        self, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document | None:
        """One visible document by (organization, id); its knowledge base must be live too."""
        document: Document | None = await self._session.scalar(
            select(Document)
            .join(
                KnowledgeBase,
                (KnowledgeBase.id == Document.knowledge_base_id)
                & (KnowledgeBase.organization_id == Document.organization_id),
            )
            .where(
                Document.organization_id == organization_id,
                Document.id == document_id,
                Document.status != DocumentStatus.DELETING,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        return document

    async def latest_job(
        self, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> ProcessingJob | None:
        job: ProcessingJob | None = await self._session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.organization_id == organization_id,
                ProcessingJob.document_id == document_id,
            )
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc())
            .limit(1)
        )
        return job

    async def lock_visible(
        self, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document | None:
        """Like ``get_visible``, but locks the document row (FOR UPDATE) until the end of the
        transaction: reprocess and delete of one document run one after the other."""
        document: Document | None = await self._session.scalar(
            select(Document)
            .join(
                KnowledgeBase,
                (KnowledgeBase.id == Document.knowledge_base_id)
                & (KnowledgeBase.organization_id == Document.organization_id),
            )
            .where(
                Document.organization_id == organization_id,
                Document.id == document_id,
                Document.status != DocumentStatus.DELETING,
                KnowledgeBase.deleted_at.is_(None),
            )
            .with_for_update(of=Document)
            .execution_options(populate_existing=True)
        )
        return document

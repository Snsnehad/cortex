"""Data access for documents. Repositories never commit.

Every lookup takes the organization **and** the knowledge base, never a document id or a
hash alone.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, document: Document) -> None:
        self._session.add(document)

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

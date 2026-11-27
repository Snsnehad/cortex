"""Data access for knowledge bases. Repositories never commit.

Every query filters on ``organization_id`` **and** excludes soft-deleted knowledge bases, so
a deleted KB disappears from every read the moment ``deleted_at`` is set.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, KnowledgeBase

# Statuses shown in stats; DELETING documents are already gone as far as users are concerned.
VISIBLE_STATUSES = (
    DocumentStatus.PENDING,
    DocumentStatus.PROCESSING,
    DocumentStatus.READY,
    DocumentStatus.FAILED,
)


@dataclass(slots=True)
class KnowledgeBaseStats:
    documents_by_status: dict[DocumentStatus, int] = field(
        default_factory=lambda: dict.fromkeys(VISIBLE_STATUSES, 0)
    )
    total_bytes: int = 0
    chunk_count: int = 0

    @property
    def document_count(self) -> int:
        return sum(self.documents_by_status.values())


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, knowledge_base: KnowledgeBase) -> None:
        self._session.add(knowledge_base)

    @staticmethod
    def active_query(organization_id: uuid.UUID) -> Select[Any]:
        """The organization's knowledge bases that are not deleted (for pagination too)."""
        return select(KnowledgeBase).where(
            KnowledgeBase.organization_id == organization_id,
            KnowledgeBase.deleted_at.is_(None),
        )

    async def get_active(
        self, organization_id: uuid.UUID, knowledge_base_id: uuid.UUID
    ) -> KnowledgeBase | None:
        """One knowledge base, by (organization, id). Never by id alone: an id from another
        organization must look exactly like an id that does not exist."""
        knowledge_base: KnowledgeBase | None = await self._session.scalar(
            self.active_query(organization_id).where(KnowledgeBase.id == knowledge_base_id)
        )
        return knowledge_base

    async def stats(
        self, organization_id: uuid.UUID, knowledge_base_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, KnowledgeBaseStats]:
        """Document statistics for several knowledge bases in ONE grouped query (no N+1)."""
        result = {kb_id: KnowledgeBaseStats() for kb_id in knowledge_base_ids}
        if not knowledge_base_ids:
            return result
        rows = await self._session.execute(
            select(
                Document.knowledge_base_id,
                Document.status,
                func.count(),
                func.coalesce(func.sum(Document.file_size_bytes), 0),
                func.coalesce(func.sum(Document.chunk_count), 0),
            )
            .where(
                Document.organization_id == organization_id,
                Document.knowledge_base_id.in_(knowledge_base_ids),
                Document.status.in_(VISIBLE_STATUSES),
            )
            .group_by(Document.knowledge_base_id, Document.status)
        )
        for kb_id, status, count, total_bytes, chunks in rows:
            entry = result[kb_id]
            entry.documents_by_status[status] = int(count)
            entry.total_bytes += int(total_bytes)
            entry.chunk_count += int(chunks)
        return result

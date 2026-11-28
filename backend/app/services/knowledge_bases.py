"""Knowledge bases (TASK-04.02, API_SPECIFICATION.md "Knowledge bases").

* Members can list and read knowledge bases; ADMIN+ can create, update and delete them (the
  routes enforce the role; this service enforces the data rules).
* Names are unique per organization, case-insensitively, among knowledge bases that are not
  deleted. The database's partial unique index decides, so two simultaneous creates with the
  same name cannot both succeed: the loser gets 409 ``KNOWLEDGE_BASE_NAME_TAKEN``.
* The embedding profile is fixed when the knowledge base is created (ADR-008).
* Chunking settings may change later; they apply to documents uploaded or reprocessed after
  the change, never retroactively.
* Delete is a **soft** delete: the knowledge base vanishes from every read at once and its
  name is free again. Removing its documents, vectors and files is a background job
  (Phase 5), which finds knowledge bases with ``deleted_at`` set.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.pagination import CursorCodec, PageResult
from app.core.tenancy import OrgContext
from app.models import Document, DocumentStatus, JobStatus, JobType, KnowledgeBase, ProcessingJob
from app.repositories.jobs import JobRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository, KnowledgeBaseStats
from app.repositories.pagination import KeysetOrder, PageRequest, fetch_page

NAME_UNIQUE_INDEX = "uq_knowledge_bases_org_name_active"
NEWEST_FIRST = KeysetOrder(KnowledgeBase.created_at, KnowledgeBase.id, descending=True)
CHUNKING_NOTICE = (
    "Chunking changes apply to documents uploaded or reprocessed from now on; existing "
    "documents keep their current chunks."
)


class _Unset(Enum):
    """Marks an argument that was not given (``None`` means "clear it")."""

    TOKEN = 0


_UNSET = _Unset.TOKEN


class KnowledgeBaseNotFoundError(NotFoundError):
    default_code = "KNOWLEDGE_BASE_NOT_FOUND"
    default_message = "Knowledge base not found."


class KnowledgeBaseNameTakenError(ConflictError):
    default_code = "KNOWLEDGE_BASE_NAME_TAKEN"
    default_message = "This organization already has a knowledge base with that name."


@dataclass(frozen=True, slots=True)
class KnowledgeBaseWithStats:
    knowledge_base: KnowledgeBase
    stats: KnowledgeBaseStats


@dataclass(frozen=True, slots=True)
class DeletedKnowledgeBase:
    deleted_at: datetime
    delete_job_ids: list[uuid.UUID]  # enqueue these after the commit


@dataclass(frozen=True, slots=True)
class UpdatedKnowledgeBase:
    item: KnowledgeBaseWithStats
    notices: list[str]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_name_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == NAME_UNIQUE_INDEX


def _check_chunking(size: int, overlap: int) -> None:
    if overlap >= size:
        raise ValidationFailedError(
            "chunk_overlap_tokens must be smaller than chunk_size_tokens.",
            details={
                "fields": [
                    {
                        "loc": ["body", "chunk_overlap_tokens"],
                        "msg": "Must be smaller than chunk_size_tokens.",
                        "type": "chunk_overlap_too_large",
                    }
                ]
            },
        )


class KnowledgeBaseService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock
        self._repository = KnowledgeBaseRepository(session)

    async def create(
        self,
        ctx: OrgContext,
        *,
        name: str,
        description: str | None,
        chunk_size_tokens: int | None,
        chunk_overlap_tokens: int | None,
    ) -> KnowledgeBaseWithStats:
        size = chunk_size_tokens or self._settings.rag.chunk_size_tokens
        overlap = (
            chunk_overlap_tokens
            if chunk_overlap_tokens is not None
            else min(self._settings.rag.chunk_overlap_tokens, size - 1)
        )
        _check_chunking(size, overlap)
        knowledge_base = KnowledgeBase(
            organization_id=ctx.organization_id,
            name=name,
            description=description,
            embedding_profile=self._settings.embeddings.profile,
            chunk_size_tokens=size,
            chunk_overlap_tokens=overlap,
            created_by_user_id=ctx.user_id,
        )
        self._repository.add(knowledge_base)
        await self._commit_or_name_conflict()
        return KnowledgeBaseWithStats(knowledge_base, KnowledgeBaseStats())

    async def list(
        self, ctx: OrgContext, *, page: PageRequest, codec: CursorCodec
    ) -> PageResult[KnowledgeBaseWithStats]:
        result = await fetch_page(
            self._session,
            self._repository.active_query(ctx.organization_id),
            NEWEST_FIRST,
            page=page,
            scope=f"kbs:org={ctx.organization_id}:created:desc",
            codec=codec,
        )
        items: Sequence[KnowledgeBase] = result.items
        stats = await self._repository.stats(ctx.organization_id, [kb.id for kb in items])
        return PageResult(
            items=[KnowledgeBaseWithStats(kb, stats[kb.id]) for kb in items],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    async def get(self, ctx: OrgContext, knowledge_base_id: uuid.UUID) -> KnowledgeBaseWithStats:
        knowledge_base = await self._get(ctx, knowledge_base_id)
        return await self._with_stats(ctx, knowledge_base)

    async def update(
        self,
        ctx: OrgContext,
        knowledge_base_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | _Unset | None = _UNSET,
        chunk_size_tokens: int | None = None,
        chunk_overlap_tokens: int | None = None,
    ) -> UpdatedKnowledgeBase:
        """Change only what is given (``description=None`` clears it)."""
        knowledge_base = await self._get(ctx, knowledge_base_id)
        size = chunk_size_tokens or knowledge_base.chunk_size_tokens
        overlap = (
            chunk_overlap_tokens
            if chunk_overlap_tokens is not None
            else knowledge_base.chunk_overlap_tokens
        )
        _check_chunking(size, overlap)
        chunking_changed = (size, overlap) != (
            knowledge_base.chunk_size_tokens,
            knowledge_base.chunk_overlap_tokens,
        )
        if name is not None:
            knowledge_base.name = name
        if not isinstance(description, _Unset):
            knowledge_base.description = description
        knowledge_base.chunk_size_tokens = size
        knowledge_base.chunk_overlap_tokens = overlap
        await self._commit_or_name_conflict()
        return UpdatedKnowledgeBase(
            item=await self._with_stats(ctx, knowledge_base),
            notices=[CHUNKING_NOTICE] if chunking_changed else [],
        )

    async def delete(self, ctx: OrgContext, knowledge_base_id: uuid.UUID) -> DeletedKnowledgeBase:
        """Soft delete. One conditional UPDATE, so of two simultaneous deletes exactly one
        succeeds and the other gets 404 (as for any knowledge base that does not exist).

        In the same transaction every live document of the knowledge base becomes DELETING,
        their active jobs are cancelled and a DELETE job is queued for each; the caller
        enqueues those after the commit. The knowledge base row itself is removed once its
        last document is gone (the delete task, or the reconciler for an empty one)."""
        now = self._clock()
        deleted = await self._session.scalar(
            update(KnowledgeBase)
            .where(
                KnowledgeBase.organization_id == ctx.organization_id,
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .returning(KnowledgeBase.id)
        )
        if deleted is None:
            await self._session.rollback()
            raise KnowledgeBaseNotFoundError
        document_ids = list(
            await self._session.scalars(
                update(Document)
                .where(
                    Document.organization_id == ctx.organization_id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status != DocumentStatus.DELETING,
                )
                .values(status=DocumentStatus.DELETING)
                .returning(Document.id)
                .execution_options(synchronize_session=False)
            )
        )
        jobs = JobRepository(self._session)
        await jobs.cancel_active_for_documents(ctx.organization_id, document_ids)
        delete_jobs = [
            ProcessingJob(
                organization_id=ctx.organization_id,
                document_id=document_id,
                job_type=JobType.DELETE,
                status=JobStatus.QUEUED,
            )
            for document_id in document_ids
        ]
        self._session.add_all(delete_jobs)
        await self._session.commit()
        return DeletedKnowledgeBase(now, [job.id for job in delete_jobs])

    async def _get(self, ctx: OrgContext, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        knowledge_base = await self._repository.get_active(ctx.organization_id, knowledge_base_id)
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError
        return knowledge_base

    async def _with_stats(
        self, ctx: OrgContext, knowledge_base: KnowledgeBase
    ) -> KnowledgeBaseWithStats:
        stats = await self._repository.stats(ctx.organization_id, [knowledge_base.id])
        return KnowledgeBaseWithStats(knowledge_base, stats[knowledge_base.id])

    async def _commit_or_name_conflict(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if _is_name_conflict(exc):
                raise KnowledgeBaseNameTakenError from exc
            raise

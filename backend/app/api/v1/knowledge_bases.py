"""/api/v1/orgs/{org_id}/knowledge-bases (TASK-04.02).

Reading needs membership; creating, changing and deleting need ADMIN. Every lookup goes
through the verified ``OrgContext`` and ``(organization_id, id)``.
"""

import uuid
from typing import Annotated

from anyio import to_thread
from fastapi import APIRouter, Depends, status

from app.dependencies.database import DbSession
from app.dependencies.orgs import AdminContext, OrgContextDep
from app.dependencies.pagination import CursorCodecDep, PageParams
from app.dependencies.queue import JobQueueDep
from app.dependencies.settings import SettingsDep
from app.models import JobType
from app.schemas.knowledge_bases import (
    KnowledgeBaseCreate,
    KnowledgeBaseDeleted,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    KnowledgeBaseUpdated,
)
from app.schemas.pagination import Page
from app.services.knowledge_bases import KnowledgeBaseService

router = APIRouter(prefix="/orgs/{org_id}/knowledge-bases", tags=["knowledge bases"])


def get_knowledge_base_service(session: DbSession, settings: SettingsDep) -> KnowledgeBaseService:
    return KnowledgeBaseService(session, settings)


KnowledgeBases = Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KnowledgeBaseOut)
async def create_knowledge_base(
    body: KnowledgeBaseCreate, ctx: AdminContext, service: KnowledgeBases
) -> KnowledgeBaseOut:
    created = await service.create(
        ctx,
        name=body.name,
        description=body.description,
        chunk_size_tokens=body.chunk_size_tokens,
        chunk_overlap_tokens=body.chunk_overlap_tokens,
    )
    return KnowledgeBaseOut.from_domain(created)


@router.get("", response_model=Page[KnowledgeBaseOut])
async def list_knowledge_bases(
    ctx: OrgContextDep, page: PageParams, codec: CursorCodecDep, service: KnowledgeBases
) -> Page[KnowledgeBaseOut]:
    result = await service.list(ctx, page=page, codec=codec)
    return Page[KnowledgeBaseOut].from_result(result, KnowledgeBaseOut.from_domain)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    kb_id: uuid.UUID, ctx: OrgContextDep, service: KnowledgeBases
) -> KnowledgeBaseOut:
    return KnowledgeBaseOut.from_domain(await service.get(ctx, kb_id))


@router.patch("/{kb_id}", response_model=KnowledgeBaseUpdated)
async def update_knowledge_base(
    kb_id: uuid.UUID, body: KnowledgeBaseUpdate, ctx: AdminContext, service: KnowledgeBases
) -> KnowledgeBaseUpdated:
    changes = body.model_dump(exclude_unset=True)
    result = await service.update(ctx, kb_id, **changes)
    return KnowledgeBaseUpdated.from_update(result)


@router.delete(
    "/{kb_id}", status_code=status.HTTP_202_ACCEPTED, response_model=KnowledgeBaseDeleted
)
async def delete_knowledge_base(
    kb_id: uuid.UUID, ctx: AdminContext, service: KnowledgeBases, queue: JobQueueDep
) -> KnowledgeBaseDeleted:
    # 202: the knowledge base is gone for users now; one DELETE job per document removes
    # their files and rows in the background (enqueued after the commit).
    deleted = await service.delete(ctx, kb_id)
    for job_id in deleted.delete_job_ids:
        await to_thread.run_sync(queue.enqueue, job_id, JobType.DELETE)
    return KnowledgeBaseDeleted(id=kb_id, deleted_at=deleted.deleted_at)

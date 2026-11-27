"""Request/response models for /api/v1/orgs/{org_id}/knowledge-bases."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.models import DocumentStatus
from app.models.knowledge_base import CHUNK_SIZE_MAX, CHUNK_SIZE_MIN
from app.services.knowledge_bases import KnowledgeBaseWithStats, UpdatedKnowledgeBase

KbName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ChunkSize = Annotated[int, Field(ge=CHUNK_SIZE_MIN, le=CHUNK_SIZE_MAX)]
ChunkOverlap = Annotated[int, Field(ge=0, lt=CHUNK_SIZE_MAX)]


class KnowledgeBaseCreate(BaseModel):
    name: KbName
    description: Description | None = None
    chunk_size_tokens: ChunkSize | None = Field(
        default=None, description="Default from server settings (512)."
    )
    chunk_overlap_tokens: ChunkOverlap | None = Field(
        default=None, description="Must be smaller than chunk_size_tokens. Default 64."
    )


class KnowledgeBaseUpdate(BaseModel):
    """Only the fields sent are changed. ``description: null`` clears the description."""

    name: KbName | None = None
    description: Description | None = None
    chunk_size_tokens: ChunkSize | None = None
    chunk_overlap_tokens: ChunkOverlap | None = None

    @model_validator(mode="after")
    def _name_cannot_be_cleared(self) -> "KnowledgeBaseUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class KnowledgeBaseStatsOut(BaseModel):
    document_count: int
    documents_by_status: dict[DocumentStatus, int]
    total_bytes: int
    chunk_count: int


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    embedding_profile: str
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    stats: KnowledgeBaseStatsOut

    @classmethod
    def from_domain(cls, item: KnowledgeBaseWithStats) -> "KnowledgeBaseOut":
        kb, stats = item.knowledge_base, item.stats
        return cls(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            embedding_profile=kb.embedding_profile,
            chunk_size_tokens=kb.chunk_size_tokens,
            chunk_overlap_tokens=kb.chunk_overlap_tokens,
            created_by_user_id=kb.created_by_user_id,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
            stats=KnowledgeBaseStatsOut(
                document_count=stats.document_count,
                documents_by_status=stats.documents_by_status,
                total_bytes=stats.total_bytes,
                chunk_count=stats.chunk_count,
            ),
        )


class KnowledgeBaseUpdated(KnowledgeBaseOut):
    notices: list[str] = Field(
        description="Things the caller should know, e.g. that chunking changes are not retroactive."
    )

    @classmethod
    def from_update(cls, result: UpdatedKnowledgeBase) -> "KnowledgeBaseUpdated":
        base = KnowledgeBaseOut.from_domain(result.item)
        return cls(**base.model_dump(), notices=result.notices)


class KnowledgeBaseDeleted(BaseModel):
    id: uuid.UUID
    deleted_at: datetime

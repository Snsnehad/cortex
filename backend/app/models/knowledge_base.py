"""Knowledge bases (DATABASE_DESIGN.md: knowledge_bases)."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document

CHUNK_SIZE_MIN, CHUNK_SIZE_MAX = 64, 2048


class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        # Target of the composite tenant foreign keys from documents (and later
        # conversations): a child row can only point at a KB of its *own* organization.
        UniqueConstraint("id", "organization_id"),
        CheckConstraint(
            f"chunk_size_tokens BETWEEN {CHUNK_SIZE_MIN} AND {CHUNK_SIZE_MAX}",
            name="chunk_size_range",
        ),
        CheckConstraint(
            "chunk_overlap_tokens >= 0 AND chunk_overlap_tokens < chunk_size_tokens",
            name="chunk_overlap_valid",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Fixed per KB; changing the embedding model means a controlled reindex (ADR-008).
    embedding_profile: Mapped[str] = mapped_column(String(120))
    chunk_size_tokens: Mapped[int] = mapped_column(server_default="512")
    chunk_overlap_tokens: Mapped[int] = mapped_column(server_default="64")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Soft delete: hidden immediately, purged by a background cleanup task.
    deleted_at: Mapped[datetime | None]

    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} org={self.organization_id}>"


# Indexes on expressions or with DESC/WHERE are defined after the class so they reference
# real columns (a plain string such as "name" inside func.lower() would be a literal).
Index(
    # KB names are unique per organization, case-insensitively, among *active* KBs; a
    # soft-deleted KB frees its name immediately.
    "uq_knowledge_bases_org_name_active",
    KnowledgeBase.organization_id,
    func.lower(KnowledgeBase.name),
    unique=True,
    postgresql_where=KnowledgeBase.deleted_at.is_(None),
)
Index(
    "ix_knowledge_bases_organization_id_created_at",
    KnowledgeBase.organization_id,
    KnowledgeBase.created_at.desc(),
)

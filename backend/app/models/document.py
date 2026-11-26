"""Documents and their chunks (DATABASE_DESIGN.md: documents, document_chunks).

Tenant integrity is enforced by the database, not only by application code:

* ``documents (knowledge_base_id, organization_id)`` → ``knowledge_bases (id, organization_id)``:
  a document cannot belong to another organization's knowledge base.
* ``document_chunks (document_id, knowledge_base_id, organization_id)`` →
  ``documents (id, knowledge_base_id, organization_id)``: a chunk's tenant and KB, which become
  the vector-search filter payload, always match its document's.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase
    from app.models.processing_job import ProcessingJob

SHA256_HEX_LENGTH = 64


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"  # hidden from every read; cleanup in progress


def chunk_id(document_id: uuid.UUID, embedding_profile: str, chunk_index: int) -> uuid.UUID:
    """Deterministic chunk id, also used as the Qdrant point id.

    Re-processing a document yields the same ids, so inserts and vector upserts overwrite
    instead of duplicating (idempotent ingestion, ARCHITECTURE.md §8).
    """
    return uuid.uuid5(document_id, f"{embedding_profile}:{chunk_index}")


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["knowledge_base_id", "organization_id"],
            ["knowledge_bases.id", "knowledge_bases.organization_id"],
            ondelete="CASCADE",
        ),
        # Targets of the composite FKs from processing_jobs and document_chunks.
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "knowledge_base_id", "organization_id"),
        CheckConstraint("file_size_bytes >= 0", name="file_size_non_negative"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        Index(None, "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID]
    knowledge_base_id: Mapped[uuid.UUID]
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Sanitised original name: display only, never used as a filesystem path.
    filename: Mapped[str] = mapped_column(String(255))
    # Detected from the file's bytes, never trusted from the client.
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(CHAR(SHA256_HEX_LENGTH))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=16,
            validate_strings=True,
        )
    )
    page_count: Mapped[int | None]
    chunk_count: Mapped[int] = mapped_column(server_default="0")
    embedding_profile: Mapped[str | None] = mapped_column(String(120))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    processed_at: Mapped[datetime | None]

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="documents", lazy="raise")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", lazy="raise", passive_deletes=True
    )
    jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="document", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        # No filename: it can contain personal data.
        return f"<Document id={self.id} kb={self.knowledge_base_id} status={self.status}>"


Index(
    # The same file may not be added twice to one KB. Documents being deleted do not count,
    # so a user can re-upload while cleanup of the old copy is still running. A partial
    # unique index is race-safe: of two concurrent identical uploads exactly one wins.
    "uq_documents_kb_content_hash_active",
    Document.knowledge_base_id,
    Document.content_hash,
    unique=True,
    postgresql_where=text("status <> 'DELETING'"),
)
Index(
    "ix_documents_knowledge_base_id_created_at",
    Document.knowledge_base_id,
    Document.created_at.desc(),
)


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "knowledge_base_id", "organization_id"],
            ["documents.id", "documents.knowledge_base_id", "documents.organization_id"],
            ondelete="CASCADE",
        ),
        # Several embedding profiles may coexist for one document during a reindex.
        UniqueConstraint("document_id", "embedding_profile", "chunk_index"),
        Index(None, "organization_id", "knowledge_base_id"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="char_range_valid"),
        CheckConstraint(
            "page_start IS NULL OR (page_start >= 1 AND page_end >= page_start)",
            name="page_range_valid",
        ),
    )

    # No default on purpose: the id must come from chunk_id(); a random fallback would
    # silently break idempotent re-indexing.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID]
    knowledge_base_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    embedding_profile: Mapped[str] = mapped_column(String(120))
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    char_start: Mapped[int]
    char_end: Mapped[int]
    page_start: Mapped[int | None]
    page_end: Mapped[int | None]
    section_path: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(CHAR(SHA256_HEX_LENGTH))
    indexed_at: Mapped[datetime | None]

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")

    def __repr__(self) -> str:
        # Never include the text.
        return f"<DocumentChunk id={self.id} doc={self.document_id} index={self.chunk_index}>"

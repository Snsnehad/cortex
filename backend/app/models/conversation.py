"""Conversations, messages and citations (DATABASE_DESIGN.md)."""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MessageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


def _string_enum(enum_cls: type[StrEnum], name: str) -> Any:
    """varchar + CHECK (not a native PostgreSQL enum), as for OrgRole."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        validate_strings=True,
    )


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chat thread, private to its creator, scoped to one knowledge base."""

    __tablename__ = "conversations"
    __table_args__ = (
        # Tenant integrity: the KB must belong to the conversation's organization.
        ForeignKeyConstraint(
            ["knowledge_base_id", "organization_id"],
            ["knowledge_bases.id", "knowledge_bases.organization_id"],
            ondelete="CASCADE",
        ),
        # Ownership integrity: only a current *member* of the organization can own a
        # conversation in it. Removing the membership deletes the member's (private,
        # now unreachable) conversations in that organization.
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.user_id",
            ],
            ondelete="CASCADE",
        ),
    )

    organization_id: Mapped[uuid.UUID]
    knowledge_base_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(String(200))

    knowledge_base: Mapped["KnowledgeBase"] = relationship(lazy="raise")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        lazy="raise",
        passive_deletes=True,
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        # No title: it is usually the user's first question.
        return f"<Conversation id={self.id} user={self.user_id}>"


Index(
    # "My conversations, most recently active first."
    "ix_conversations_org_user_updated_at",
    Conversation.organization_id,
    Conversation.user_id,
    Conversation.updated_at.desc(),
)


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(None, "conversation_id", "created_at"),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0)"
            " AND (completion_tokens IS NULL OR completion_tokens >= 0)",
            name="tokens_non_negative",
        ),
        CheckConstraint(
            "(retrieval_latency_ms IS NULL OR retrieval_latency_ms >= 0)"
            " AND (rerank_latency_ms IS NULL OR rerank_latency_ms >= 0)"
            " AND (generation_latency_ms IS NULL OR generation_latency_ms >= 0)"
            " AND (total_latency_ms IS NULL OR total_latency_ms >= 0)",
            name="latencies_non_negative",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(_string_enum(MessageRole, "role"))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        _string_enum(MessageStatus, "status"), server_default=MessageStatus.COMPLETE.value
    )
    # The condensed, self-contained question actually used for retrieval (follow-ups).
    standalone_query: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    retrieval_latency_ms: Mapped[int | None]
    rerank_latency_ms: Mapped[int | None]
    generation_latency_ms: Mapped[int | None]
    total_latency_ms: Mapped[int | None]
    # Candidate ids and scores per retrieval stage, for debugging and evaluation.
    # Never document text.
    retrieval_trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")
    citations: Mapped[list["MessageCitation"]] = relationship(
        back_populates="message",
        lazy="raise",
        passive_deletes=True,
        order_by="MessageCitation.marker",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role} status={self.status}>"


class MessageCitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A source cited by an assistant message as ``[S<marker>]``.

    Chat history must stay readable after the cited document is deleted, so the links to the
    chunk and document become NULL while the filename, pages and snippet are kept as a
    snapshot.
    """

    __tablename__ = "message_citations"
    __table_args__ = (
        # One citation per marker per message; the index also serves lookups by message_id.
        UniqueConstraint("message_id", "marker"),
        CheckConstraint("marker >= 1", name="marker_positive"),
        CheckConstraint(
            "page_start IS NULL OR (page_start >= 1 AND page_end >= page_start)",
            name="page_range_valid",
        ),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    marker: Mapped[int]
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    document_filename: Mapped[str] = mapped_column(String(255))
    page_start: Mapped[int | None]
    page_end: Mapped[int | None]
    section_path: Mapped[str | None] = mapped_column(String(1000))
    snippet: Mapped[str] = mapped_column(String(1000))
    # REAL (4-byte float) is plenty for a reranker score. Declared as REAL, not Float(24):
    # the database reflects REAL, and the drift test flags the mismatch otherwise.
    relevance_score: Mapped[float | None] = mapped_column(REAL)

    message: Mapped[Message] = relationship(back_populates="citations", lazy="raise")

    def __repr__(self) -> str:
        return f"<MessageCitation message={self.message_id} marker={self.marker}>"

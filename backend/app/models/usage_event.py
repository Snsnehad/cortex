"""Usage events: tokens, estimated cost and latency per AI call (DATABASE_DESIGN.md)."""

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# numeric(12, 6): up to 999,999.999999 USD per event, exact to a millionth of a dollar.
# Never float: binary floating point cannot represent 0.1 exactly, and summing millions of
# tiny per-call costs would drift.
COST_PRECISION, COST_SCALE = 12, 6


class UsageEventType(StrEnum):
    DOC_EMBEDDING = "DOC_EMBEDDING"
    QUERY_EMBEDDING = "QUERY_EMBEDDING"
    LLM_COMPLETION = "LLM_COMPLETION"
    QUERY_REWRITE = "QUERY_REWRITE"
    RERANK = "RERANK"


class UsageEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="tokens_non_negative"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_type: Mapped[UsageEventType] = mapped_column(
        Enum(
            UsageEventType,
            name="event_type",
            native_enum=False,
            create_constraint=True,
            length=32,
            validate_strings=True,
        )
    )
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(COST_PRECISION, COST_SCALE), server_default="0"
    )
    latency_ms: Mapped[int | None]
    # Deliberately no foreign keys: usage and cost history must survive the deletion of the
    # document or message that caused it (billing and analytics are append-only facts).
    document_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]

    def __repr__(self) -> str:
        return f"<UsageEvent id={self.id} type={self.event_type} cost={self.cost_usd}>"


Index(
    "ix_usage_events_org_created_at",
    UsageEvent.organization_id,
    UsageEvent.created_at,
)
Index(
    "ix_usage_events_org_type_created_at",
    UsageEvent.organization_id,
    UsageEvent.event_type,
    UsageEvent.created_at,
)

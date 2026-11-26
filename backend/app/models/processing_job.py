"""Background processing jobs (DATABASE_DESIGN.md: processing_jobs, ARCHITECTURE.md §8)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document


class JobType(StrEnum):
    INGEST = "INGEST"
    REINDEX = "REINDEX"
    DELETE = "DELETE"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_JOB_STATUSES


ACTIVE_JOB_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING})


class JobStage(StrEnum):
    EXTRACTING = "EXTRACTING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"


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


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_valid"),
        # The reconciler scans for stale jobs: WHERE status = ? AND lease_expires_at < now()
        Index(None, "status", "lease_expires_at"),
    )

    organization_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    job_type: Mapped[JobType] = mapped_column(_string_enum(JobType, "job_type"))
    status: Mapped[JobStatus] = mapped_column(_string_enum(JobStatus, "status"))
    stage: Mapped[JobStage | None] = mapped_column(_string_enum(JobStage, "stage"))
    attempts: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default="5")
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    # Crash detection: a RUNNING job whose lease expired belongs to a dead worker.
    lease_expires_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    # Per-stage durations and counts (pages, chunks, tokens). Never document text.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    document: Mapped["Document"] = relationship(back_populates="jobs", lazy="raise")

    def __repr__(self) -> str:
        return f"<ProcessingJob id={self.id} doc={self.document_id} status={self.status}>"


Index(
    # At most ONE active job per document, enforced by the database: two workers (or a
    # double-clicked "reprocess") can never process the same document concurrently.
    "uq_processing_jobs_document_id_active",
    ProcessingJob.document_id,
    unique=True,
    postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'RETRYING')"),
)
Index(
    "ix_processing_jobs_document_id_created_at",
    ProcessingJob.document_id,
    ProcessingJob.created_at.desc(),
)

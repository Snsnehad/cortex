"""SQLAlchemy ORM models, one module per aggregate.

Model registry: import every model module here. Alembic's env.py imports this package so
that autogenerate sees every table on ``Base.metadata``; a model that is not imported here
would be invisible to migrations (and the drift test in tests/integration/test_migrations.py
would not catch it).
"""

from app.models.conversation import (
    Conversation,
    Message,
    MessageCitation,
    MessageRole,
    MessageStatus,
)
from app.models.document import Document, DocumentChunk, DocumentStatus, chunk_id
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization, OrganizationMembership, OrgRole
from app.models.processing_job import (
    ACTIVE_JOB_STATUSES,
    JobStage,
    JobStatus,
    JobType,
    ProcessingJob,
)
from app.models.refresh_token import RefreshToken
from app.models.usage_event import UsageEvent, UsageEventType
from app.models.user import User

__all__ = [
    "ACTIVE_JOB_STATUSES",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "JobStage",
    "JobStatus",
    "JobType",
    "KnowledgeBase",
    "Message",
    "MessageCitation",
    "MessageRole",
    "MessageStatus",
    "OrgRole",
    "Organization",
    "OrganizationMembership",
    "ProcessingJob",
    "RefreshToken",
    "UsageEvent",
    "UsageEventType",
    "User",
    "chunk_id",
]

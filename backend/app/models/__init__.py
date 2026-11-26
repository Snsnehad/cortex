"""SQLAlchemy ORM models, one module per aggregate.

Model registry: import every model module here. Alembic's env.py imports this package so
that autogenerate sees every table on ``Base.metadata``; a model that is not imported here
would be invisible to migrations (and the drift test in tests/integration/test_migrations.py
would not catch it).
"""

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
from app.models.user import User

__all__ = [
    "ACTIVE_JOB_STATUSES",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "JobStage",
    "JobStatus",
    "JobType",
    "KnowledgeBase",
    "OrgRole",
    "Organization",
    "OrganizationMembership",
    "ProcessingJob",
    "RefreshToken",
    "User",
    "chunk_id",
]

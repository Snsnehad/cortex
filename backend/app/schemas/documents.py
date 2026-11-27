"""Response models for documents and processing jobs (API_SPECIFICATION.md "Documents").

The storage key is internal and never returned.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models import Document, DocumentStatus, JobStage, JobStatus, JobType, ProcessingJob
from app.services.documents import AddedDocument, DocumentWithJob


class DocumentOut(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    mime_type: str
    file_size_bytes: int
    content_hash: str
    status: DocumentStatus
    page_count: int | None
    chunk_count: int
    error_code: str | None
    error_message: str | None
    uploaded_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None

    @classmethod
    def from_model(cls, document: Document) -> "DocumentOut":
        return cls(
            id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            filename=document.filename,
            mime_type=document.mime_type,
            file_size_bytes=document.file_size_bytes,
            content_hash=document.content_hash,
            status=document.status,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            error_code=document.error_code,
            error_message=document.error_message,
            uploaded_by_user_id=document.uploaded_by_user_id,
            created_at=document.created_at,
            updated_at=document.updated_at,
            processed_at=document.processed_at,
        )


class JobOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    stage: JobStage | None
    attempts: int
    max_attempts: int
    error_code: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, job: ProcessingJob) -> "JobOut":
        return cls(
            id=job.id,
            document_id=job.document_id,
            job_type=job.job_type,
            status=job.status,
            stage=job.stage,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error_code=job.error_code,
            created_at=job.created_at,
        )


class DocumentAccepted(BaseModel):
    """202 body: the document is stored and queued for processing."""

    document: DocumentOut
    job: JobOut

    @classmethod
    def from_domain(cls, added: AddedDocument) -> "DocumentAccepted":
        return cls(
            document=DocumentOut.from_model(added.document), job=JobOut.from_model(added.job)
        )


class DocumentDetail(DocumentOut):
    latest_job: JobOut | None

    @classmethod
    def from_domain(cls, item: DocumentWithJob) -> "DocumentDetail":
        base = DocumentOut.from_model(item.document)
        latest = JobOut.from_model(item.job) if item.job else None
        return cls(**base.model_dump(), latest_job=latest)


class DocumentStatusOut(BaseModel):
    """Lightweight poll while a document is processed."""

    document_id: uuid.UUID
    status: DocumentStatus
    job_status: JobStatus | None
    stage: JobStage | None
    attempts: int
    max_attempts: int | None
    error_code: str | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, item: DocumentWithJob) -> "DocumentStatusOut":
        document, job = item.document, item.job
        return cls(
            document_id=document.id,
            status=document.status,
            job_status=job.status if job else None,
            stage=job.stage if job else None,
            attempts=job.attempts if job else 0,
            max_attempts=job.max_attempts if job else None,
            error_code=document.error_code or (job.error_code if job else None),
            updated_at=max(document.updated_at, job.updated_at) if job else document.updated_at,
        )

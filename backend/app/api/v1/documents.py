"""/api/v1/orgs/{org_id}/.../documents: upload (TASK-04.05); list, get, status, download
(TASK-04.06). Delete and reprocess come with the cleanup worker (TASK-05.05).

Reads show only *visible* documents: not ``DELETING`` and in a knowledge base that is not
deleted. Anything else, including another organization's document, is 404
``DOCUMENT_NOT_FOUND``.

Upload order matters, cheapest refusal first:

1. membership (``OrgContextDep``) → 404 for another organization;
2. per-user upload rate limit → 429 (fails open if Redis is down: a soft quota);
3. the knowledge base exists in this organization → 404, *before* reading the body;
4. the body is streamed with the size limit enforced on arrival (ADR-023) → 400/413;
5. the type is detected from the bytes → 415/422;
6. ``DocumentService.add``: duplicate check, storage, document + job in one transaction
   → 409 or **202** ``{document, job}``.

The route takes the raw ``Request`` instead of an ``UploadFile`` parameter on purpose:
``UploadFile`` would make FastAPI receive the whole body before step 1 even runs.
"""

import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from app.dependencies.database import DbSession
from app.dependencies.orgs import OrgContextDep
from app.dependencies.pagination import CursorCodecDep, PageParams
from app.dependencies.rate_limit import RateLimitsDep
from app.dependencies.settings import SettingsDep
from app.dependencies.storage import StorageDep
from app.models import DocumentStatus
from app.schemas.documents import DocumentAccepted, DocumentDetail, DocumentOut, DocumentStatusOut
from app.schemas.pagination import Page
from app.services.documents import DocumentService
from app.storage.base import content_disposition
from app.uploads import detect_upload, receive_upload

router = APIRouter(prefix="/orgs/{org_id}", tags=["documents"])

# FastAPI cannot infer the body of a route that reads the stream itself; describe it for
# the OpenAPI docs (and Swagger UI's file picker).
_MULTIPART_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "PDF, DOCX, UTF-8 text or Markdown.",
                        }
                    },
                }
            }
        },
    }
}


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentAccepted,
    openapi_extra=_MULTIPART_BODY,
    summary="Upload a document (stored now, processed in the background)",
)
async def upload_document(
    kb_id: uuid.UUID,
    request: Request,
    ctx: OrgContextDep,
    session: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
    rate_limits: RateLimitsDep,
) -> DocumentAccepted:
    await rate_limits.check(rate_limits.upload_user, str(ctx.user_id), fail_closed=False)
    service = DocumentService(session, storage)
    await service.require_knowledge_base(ctx, kb_id)
    upload = await receive_upload(request, max_bytes=settings.storage.max_upload_bytes)
    try:
        detected = await detect_upload(upload)
        added = await service.add(ctx, kb_id, upload, detected)
    finally:
        upload.close()
    return DocumentAccepted.from_domain(added)


class VisibleStatus(StrEnum):
    """Status filter values (DELETING documents are never listed)."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


@router.get("/knowledge-bases/{kb_id}/documents", response_model=Page[DocumentOut])
async def list_documents(
    kb_id: uuid.UUID,
    ctx: OrgContextDep,
    session: DbSession,
    storage: StorageDep,
    page: PageParams,
    codec: CursorCodecDep,
    status_filter: Annotated[VisibleStatus | None, Query(alias="status")] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Case-insensitive search in the filename."),
    ] = None,
) -> Page[DocumentOut]:
    result = await DocumentService(session, storage).list_in_kb(
        ctx,
        kb_id,
        status=DocumentStatus(status_filter) if status_filter else None,
        filename_contains=q.strip() if q and q.strip() else None,
        page=page,
        codec=codec,
    )
    return Page[DocumentOut].from_result(result, DocumentOut.from_model)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, ctx: OrgContextDep, session: DbSession, storage: StorageDep
) -> DocumentDetail:
    return DocumentDetail.from_domain(await DocumentService(session, storage).get(ctx, document_id))


@router.get("/documents/{document_id}/status", response_model=DocumentStatusOut)
async def get_document_status(
    document_id: uuid.UUID, ctx: OrgContextDep, session: DbSession, storage: StorageDep
) -> DocumentStatusOut:
    item = await DocumentService(session, storage).get(ctx, document_id)
    return DocumentStatusOut.from_domain(item)


@router.get(
    "/documents/{document_id}/download",
    response_class=Response,
    responses={
        200: {"description": "The original file (local storage), always as an attachment."},
        302: {"description": "Redirect to a short-lived presigned URL (S3)."},
    },
)
async def download_document(
    document_id: uuid.UUID,
    ctx: OrgContextDep,
    session: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    download = await DocumentService(session, storage).download(
        ctx, document_id, link_ttl_seconds=settings.storage.presigned_url_ttl_seconds
    )
    # Never cached: the response is private, and a presigned URL expires.
    no_store = {"Cache-Control": "private, no-store"}
    if download.redirect_url is not None:
        return RedirectResponse(download.redirect_url, status_code=302, headers=no_store)
    document = download.document
    if download.chunks is None:  # one of the two is always set
        raise RuntimeError("download has neither a redirect nor a stream")
    return StreamingResponse(
        download.chunks,
        media_type=document.mime_type,
        headers={
            **no_store,
            "Content-Disposition": content_disposition(document.filename),
            "Content-Length": str(document.file_size_bytes),
            # Browsers must not "sniff" an HTML or script type out of the bytes.
            "X-Content-Type-Options": "nosniff",
        },
    )

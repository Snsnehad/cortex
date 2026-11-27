"""/api/v1/orgs/{org_id}/.../documents (TASK-04.05; list/get/download: TASK-04.06).

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

from fastapi import APIRouter, Request, status

from app.dependencies.database import DbSession
from app.dependencies.orgs import OrgContextDep
from app.dependencies.rate_limit import RateLimitsDep
from app.dependencies.settings import SettingsDep
from app.dependencies.storage import StorageDep
from app.schemas.documents import DocumentAccepted
from app.services.documents import DocumentService
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

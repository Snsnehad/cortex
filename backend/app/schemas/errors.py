"""The error envelope returned by every non-2xx response (API_SPECIFICATION.md §1)."""

from typing import Any

from pydantic import BaseModel, Field


class FieldError(BaseModel):
    loc: list[str | int] = Field(description='Where the problem is, e.g. ["body", "email"].')
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code, e.g. DOCUMENT_DUPLICATE.")
    message: str = Field(description="Human-readable explanation, safe to show to users.")
    details: dict[str, Any] | None = None
    request_id: str | None = Field(
        default=None, description="Matches the X-Request-ID header; quote it when reporting."
    )


class ErrorResponse(BaseModel):
    error: ErrorBody

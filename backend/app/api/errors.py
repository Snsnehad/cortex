"""Maps exceptions to HTTP responses with the standard error envelope (the only such place).

* Domain errors (``app.core.errors``) → their status code and code.
* Framework errors (unknown route, wrong method, schema validation) → the same envelope.
* Unexpected exceptions are handled by ``RequestContextMiddleware`` (it logs them once with
  the request id and returns ``internal_error_response()``).

Security rules enforced here:

* FastAPI's default 422 echoes the submitted ``input`` (a failed login would echo the
  password). Field errors here carry only ``loc``, ``msg`` and ``type``.
* Internal errors return a fixed, generic message: never exception text, SQL or tracebacks.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitedError,
    UnsupportedMediaTypeError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.schemas.errors import ErrorBody, ErrorResponse, FieldError

logger = get_logger("cortex.errors")

# Order matters only for subclasses of each other (none today); first match wins.
STATUS_BY_ERROR: dict[type[AppError], int] = {
    AuthenticationError: 401,
    PermissionDeniedError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    PayloadTooLargeError: 413,
    UnsupportedMediaTypeError: 415,
    ValidationFailedError: 422,
    RateLimitedError: 429,
    UpstreamUnavailableError: 503,
}

INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred. Please retry or contact support."


def status_for(error: AppError) -> int:
    for error_type, status in STATUS_BY_ERROR.items():
        if isinstance(error, error_type):
            return status
    return 400  # a plain AppError: the client's request could not be completed


def _current_request_id() -> str | None:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return str(request_id) if request_id else None


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, details=details, request_id=_current_request_id()
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def internal_error_response() -> JSONResponse:
    return error_response(500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE)


async def handle_app_error(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # registered for AppError only
        raise TypeError(exc)
    status_code = status_for(exc)
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "Bearer"
    # Expected errors: one line with the code, no traceback. The message is not logged; it
    # can contain user input (e.g. a rejected file name).
    log = logger.warning if status_code >= 500 else logger.info
    log("http.app_error", code=exc.code, status=status_code, error_type=type(exc).__name__)
    return error_response(
        status_code, exc.code, exc.message, details=exc.details, headers=headers or None
    )


async def handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    """Unknown routes (404), wrong methods (405) and any HTTPException raised by routes."""
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(exc)
    status = HTTPStatus(exc.status_code)
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
        exc.status_code, status.phrase.upper().replace(" ", "_").replace("-", "_")
    )
    # Starlette's default detail is the status phrase; a route may set a custom string.
    message = exc.detail
    if exc.status_code == 404 and exc.detail == status.phrase:
        message = NotFoundError.default_message
    return error_response(exc.status_code, code, message, headers=exc.headers)


def _field_errors(exc: RequestValidationError) -> list[FieldError]:
    # Only loc/msg/type: FastAPI's raw errors include "input" (the submitted value, possibly
    # a password) and "ctx" (may hold non-serialisable exception objects).
    return [
        FieldError(
            loc=list(error.get("loc", ())),
            msg=str(error.get("msg", "")),
            type=str(error.get("type", "")),
        )
        for error in exc.errors()
    ]


async def handle_request_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(exc)
    fields = _field_errors(exc)
    return error_response(
        422,
        ValidationFailedError.default_code,
        "The request is invalid. See details.fields.",
        details={"fields": [field.model_dump(mode="json") for field in fields]},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)

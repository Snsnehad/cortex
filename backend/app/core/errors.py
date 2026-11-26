"""Domain exceptions.

Services raise these; they know nothing about HTTP. The API layer maps each class to a status
code and the standard error envelope in one place (``app/api/errors.py``), so a service is
reusable from a Celery task or a CLI without dragging FastAPI along.

Every error carries:

* ``code``: a stable, machine-readable identifier (``DOCUMENT_DUPLICATE``). Clients branch on
  it; it never changes once published.
* ``message``: safe, human-readable text. It is returned to the client, so it must never
  contain secrets, SQL, stack traces or another tenant's data.
* ``details``: optional structured data for the client (also returned, same rules).

Usage::

    raise NotFoundError("Knowledge base not found", code="KNOWLEDGE_BASE_NOT_FOUND")
    raise ConflictError(
        "This file already exists in the knowledge base.",
        code="DOCUMENT_DUPLICATE",
        details={"existing_document_id": str(existing.id)},
    )
"""

from typing import Any, ClassVar


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    default_code: ClassVar[str] = "ERROR"
    default_message: ClassVar[str] = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.details = details
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class AuthenticationError(AppError):
    """No valid credentials (missing, expired or invalid token)."""

    default_code = "UNAUTHENTICATED"
    default_message = "Authentication is required."


class PermissionDeniedError(AppError):
    """Authenticated, but the caller's role does not allow this action."""

    default_code = "PERMISSION_DENIED"
    default_message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    """The resource does not exist *or is not visible to the caller*.

    Cross-tenant access deliberately raises this (not PermissionDeniedError), so the API
    never confirms that another organization's resource exists.
    """

    default_code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    """The request conflicts with the current state (duplicate, last owner, active job)."""

    default_code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


class ValidationFailedError(AppError):
    """Input is well-formed but violates a business rule (schema validation is FastAPI's)."""

    default_code = "VALIDATION_ERROR"
    default_message = "The request is invalid."


class PayloadTooLargeError(AppError):
    default_code = "PAYLOAD_TOO_LARGE"
    default_message = "The request body is too large."


class UnsupportedMediaTypeError(AppError):
    default_code = "UNSUPPORTED_MEDIA_TYPE"
    default_message = "The file type is not supported."


class RateLimitedError(AppError):
    default_code = "RATE_LIMITED"
    default_message = "Too many requests. Please retry later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.retry_after_seconds = max(1, retry_after_seconds)


class UpstreamUnavailableError(AppError):
    """A dependency (LLM provider, vector store, ...) is unavailable; the caller may retry."""

    default_code = "UPSTREAM_UNAVAILABLE"
    default_message = "A required service is temporarily unavailable. Please retry."

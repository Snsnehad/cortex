"""Request id + access log middleware.

For every HTTP request:

1. Take the client's ``X-Request-ID`` if it is safe (``[A-Za-z0-9._-]``, 1 to 128 chars); otherwise
   generate one. Rejecting other characters prevents log injection (newlines, JSON breaking).
2. Bind it into structlog's context variables, so *every* log line emitted while handling the
   request (routes, services, libraries) carries ``request_id`` automatically.
3. Return it in the ``X-Request-ID`` response header, so a user-reported error can be matched
   to server logs.
4. Emit one access-log event: method, path, status, duration. The query string is never
   logged because it can carry tokens or personal data.
5. Turn any unexpected exception into the generic 500 error envelope, logging the traceback
   exactly once, with the request id (TASK-02.06).

Written as pure ASGI rather than Starlette's ``BaseHTTPMiddleware``, which has known problems
with streaming responses (SSE, planned for chat) and context-variable propagation.
"""

import re
import time
import uuid
from collections.abc import Collection

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import internal_error_response
from app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = get_logger("cortex.access")


def _incoming_request_id(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", []):
        if name == wanted:
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, quiet_paths: Collection[str] = ()) -> None:
        self.app = app
        # Paths logged at DEBUG instead of INFO when successful (health probes every few
        # seconds would otherwise drown real traffic).
        self.quiet_paths = frozenset(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        status_code = 500  # if the app fails before sending a response
        response_started = False
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                status_code = 500
                # Logged exactly once, here, inside the request context so the traceback
                # carries the request_id.
                logger.exception("http.unhandled_exception")
                if response_started:
                    # Too late for a clean error response (e.g. mid-stream): let the server
                    # abort the connection.
                    raise
                # Answer with the generic envelope ourselves and do NOT re-raise: otherwise
                # Starlette and uvicorn would each log the same traceback again, without
                # the request id.
                await internal_error_response()(scope, receive, send_with_request_id)
            finally:
                self._log_access(scope, status_code, started)

    def _log_access(self, scope: Scope, status_code: int, started: float) -> None:
        path: str = scope.get("path", "")
        client = scope.get("client")
        fields = {
            "method": scope.get("method"),
            "path": path,
            "status": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "client_ip": client[0] if client else None,
        }
        if status_code >= 500:
            logger.error("http.request", **fields)
        elif path in self.quiet_paths:
            logger.debug("http.request", **fields)
        else:
            logger.info("http.request", **fields)

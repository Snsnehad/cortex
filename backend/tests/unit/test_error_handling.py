"""Error envelope, status mapping and safe failure behaviour (TASK-02.06)."""

import inspect
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import app.core.errors as errors_module
from app.api.errors import INTERNAL_ERROR_CODE, STATUS_BY_ERROR, status_for
from app.config import Settings
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
from app.main import create_app
from app.middleware.request_context import REQUEST_ID_HEADER

pytestmark = pytest.mark.anyio

SECRET = "Sup3r-S3cret-Pw!"


class SignupBody(BaseModel):
    email: str
    password: str = Field(min_length=20)


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def build_app() -> FastAPI:
    app = create_app(Settings(_env_file=None))
    raisers: dict[str, AppError] = {
        "unauthenticated": AuthenticationError(),
        "forbidden": PermissionDeniedError(),
        "missing": NotFoundError("Knowledge base not found", code="KNOWLEDGE_BASE_NOT_FOUND"),
        "conflict": ConflictError(
            "This file already exists in the knowledge base.",
            code="DOCUMENT_DUPLICATE",
            details={"existing_document_id": "doc-123"},
        ),
        "too-large": PayloadTooLargeError(code="FILE_TOO_LARGE"),
        "unsupported": UnsupportedMediaTypeError(code="UNSUPPORTED_FILE_TYPE"),
        "invalid": ValidationFailedError("Chunk overlap must be smaller than chunk size."),
        "throttled": RateLimitedError(retry_after_seconds=30),
        "upstream": UpstreamUnavailableError(code="LLM_UNAVAILABLE"),
        "generic": AppError(),
    }

    @app.get("/_test/raise/{name}")
    async def raise_app_error(name: str) -> None:
        raise raisers[name]

    @app.post("/_test/signup")
    async def signup(body: SignupBody) -> dict[str, str]:
        return {"email": body.email}

    @app.get("/_test/http-exception")
    async def http_exception() -> None:
        raise HTTPException(status_code=409, detail="Custom route conflict")

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError(f"connect to postgresql://cortex:{SECRET}@db/cortex failed")

    @app.get("/_test/crash-mid-stream")
    async def crash_mid_stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("stream broke")

        return StreamingResponse(body())

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=True (the default): if an exception escaped the app, this client
    # would raise it, so a clean 500 response proves the middleware handled it.
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def assert_envelope(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]
    return error


def log_lines(output: str, event: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == event]


# --- domain errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("unauthenticated", 401, "UNAUTHENTICATED"),
        ("forbidden", 403, "PERMISSION_DENIED"),
        ("missing", 404, "KNOWLEDGE_BASE_NOT_FOUND"),
        ("conflict", 409, "DOCUMENT_DUPLICATE"),
        ("too-large", 413, "FILE_TOO_LARGE"),
        ("unsupported", 415, "UNSUPPORTED_FILE_TYPE"),
        ("invalid", 422, "VALIDATION_ERROR"),
        ("throttled", 429, "RATE_LIMITED"),
        ("upstream", 503, "LLM_UNAVAILABLE"),
        ("generic", 400, "ERROR"),
    ],
)
async def test_domain_errors_map_to_status_and_envelope(
    client: httpx.AsyncClient, name: str, status: int, code: str
) -> None:
    assert_envelope(await client.get(f"/_test/raise/{name}"), status, code)


async def test_message_and_details_reach_the_client(client: httpx.AsyncClient) -> None:
    error = assert_envelope(await client.get("/_test/raise/conflict"), 409, "DOCUMENT_DUPLICATE")

    assert error["message"] == "This file already exists in the knowledge base."
    assert error["details"] == {"existing_document_id": "doc-123"}


async def test_rate_limit_sets_retry_after(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/raise/throttled")

    assert response.headers["retry-after"] == "30"


async def test_unauthenticated_sets_www_authenticate(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/raise/unauthenticated")

    assert response.headers["www-authenticate"] == "Bearer"


async def test_client_request_id_is_echoed_in_the_error_body(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/raise/missing", headers={REQUEST_ID_HEADER: "req-42"})

    assert response.json()["error"]["request_id"] == "req-42"


async def test_expected_errors_log_one_line_without_message_or_traceback(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    await client.get("/_test/raise/conflict")
    output = capsys.readouterr().out

    [line] = log_lines(output, "http.app_error")
    assert (line["code"], line["status"], line["level"]) == ("DOCUMENT_DUPLICATE", 409, "info")
    assert "already exists" not in output  # messages can contain user input
    assert "Traceback" not in output


# --- framework errors --------------------------------------------------------------------------


async def test_unknown_route_returns_404_envelope(client: httpx.AsyncClient) -> None:
    error = assert_envelope(await client.get("/api/v1/no-such-thing"), 404, "NOT_FOUND")

    assert error["message"] == NotFoundError.default_message


async def test_wrong_method_returns_405_envelope_with_allow(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/v1/health/live")

    assert_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


async def test_http_exception_from_a_route_uses_the_envelope(client: httpx.AsyncClient) -> None:
    error = assert_envelope(await client.get("/_test/http-exception"), 409, "CONFLICT")

    assert error["message"] == "Custom route conflict"


async def test_validation_errors_list_fields_without_echoing_input(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/_test/signup", json={"email": "alice@example.com", "password": SECRET}
    )

    error = assert_envelope(response, 422, "VALIDATION_ERROR")
    [field] = error["details"]["fields"]
    assert field["loc"] == ["body", "password"]
    assert field["type"] == "string_too_short"
    assert set(field) == {"loc", "msg", "type"}
    # FastAPI's default 422 would include {"input": "<the password>"}.
    assert SECRET not in response.text


async def test_malformed_json_returns_422_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/_test/signup",
        content=b'{"email": "a@b.c", "password": "' + SECRET.encode() + b'"',
        headers={"content-type": "application/json"},
    )

    error = assert_envelope(response, 422, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["type"] == "json_invalid"
    assert SECRET not in response.text


# --- unexpected errors ------------------------------------------------------------------------


async def test_unhandled_exception_returns_generic_500_without_leaks(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/crash", headers={REQUEST_ID_HEADER: "req-500"})

    error = assert_envelope(response, 500, INTERNAL_ERROR_CODE)
    assert error["request_id"] == "req-500"
    for leak in (SECRET, "postgresql://", "RuntimeError", "Traceback", "crash"):
        assert leak not in response.text


async def test_unhandled_exception_is_logged_exactly_once_with_redaction(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    await client.get("/_test/crash", headers={REQUEST_ID_HEADER: "req-crash"})
    output = capsys.readouterr().out

    [logged] = log_lines(output, "http.unhandled_exception")
    assert logged["request_id"] == "req-crash"
    assert logged["exception"][0]["exc_type"] == "RuntimeError"
    assert SECRET not in output  # URL credentials redacted inside the traceback
    [access] = log_lines(output, "http.request")
    assert (access["status"], access["level"]) == (500, "error")


async def test_failure_after_the_response_started_aborts_the_stream(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # Headers are already sent; a clean error response is impossible. The exception is
    # logged once and re-raised so the server aborts the connection.
    capsys.readouterr()
    with pytest.raises(RuntimeError, match="stream broke"):
        await client.get("/_test/crash-mid-stream")

    assert len(log_lines(capsys.readouterr().out, "http.unhandled_exception")) == 1


# --- structure ---------------------------------------------------------------------------------


def test_every_domain_error_has_a_status_mapping() -> None:
    subclasses = {
        cls
        for _, cls in inspect.getmembers(errors_module, inspect.isclass)
        if issubclass(cls, AppError) and cls is not AppError
    }

    assert subclasses == set(STATUS_BY_ERROR), "add new AppError subclasses to STATUS_BY_ERROR"


def test_domain_errors_do_not_depend_on_the_web_framework() -> None:
    source = inspect.getsource(errors_module)

    assert "fastapi" not in source
    assert "starlette" not in source


def test_status_for_subclass_of_a_mapped_error() -> None:
    class KnowledgeBaseMissingError(NotFoundError):
        default_code = "KNOWLEDGE_BASE_NOT_FOUND"

    assert status_for(KnowledgeBaseMissingError()) == 404


def test_retry_after_is_at_least_one_second() -> None:
    assert RateLimitedError(retry_after_seconds=0).retry_after_seconds == 1

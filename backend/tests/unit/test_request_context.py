"""Request id propagation and access logging (TASK-01.07)."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI

from app.config import Settings
from app.core.logging import get_logger
from app.main import create_app
from app.middleware.request_context import REQUEST_ID_HEADER

pytestmark = pytest.mark.anyio

GENERATED_ID = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def build_app(**overrides: Any) -> FastAPI:
    app = create_app(Settings(_env_file=None, **overrides))
    route_logger = get_logger("cortex.test.route")

    @app.get("/api/v1/_test/log")
    async def log_something(delay: float = 0.0) -> dict[str, str]:
        await asyncio.sleep(delay)
        route_logger.info("route.handled")
        return {"ok": "yes"}

    @app.get("/api/v1/_test/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def events(output: str, name: str) -> list[dict[str, Any]]:
    return [line for line in json_lines(output) if line["event"] == name]


async def test_request_id_is_generated_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/_test/log")

    assert GENERATED_ID.fullmatch(response.headers[REQUEST_ID_HEADER])


async def test_valid_incoming_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/_test/log", headers={REQUEST_ID_HEADER: "abc-123.X_y"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123.X_y"


@pytest.mark.parametrize(
    "unsafe",
    ["has spaces", 'quote"inside', "a" * 129, '{"json":1}'],
)
async def test_unsafe_incoming_request_id_is_replaced(
    client: httpx.AsyncClient, unsafe: str
) -> None:
    response = await client.get("/api/v1/_test/log", headers={REQUEST_ID_HEADER: unsafe})

    assert GENERATED_ID.fullmatch(response.headers[REQUEST_ID_HEADER])


async def test_all_logs_of_a_request_share_its_id(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    response = await client.get("/api/v1/_test/log")
    output = capsys.readouterr().out

    request_id = response.headers[REQUEST_ID_HEADER]
    [route_line] = events(output, "route.handled")
    [access_line] = events(output, "http.request")
    assert route_line["request_id"] == request_id
    assert access_line["request_id"] == request_id


async def test_access_log_fields_and_no_query_string(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    await client.get("/api/v1/_test/log?token=very-secret-value&q=personal")
    output = capsys.readouterr().out

    [access] = events(output, "http.request")
    assert access["level"] == "info"
    assert access["logger"] == "cortex.access"
    assert access["method"] == "GET"
    assert access["path"] == "/api/v1/_test/log"
    assert access["status"] == 200
    assert isinstance(access["duration_ms"], float)
    assert "very-secret-value" not in output
    assert "personal" not in output


async def test_health_probes_are_quiet_at_info_level(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    await client.get("/api/v1/health/live")

    assert events(capsys.readouterr().out, "http.request") == []


async def test_health_probes_are_visible_at_debug_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = httpx.ASGITransport(app=build_app(log_level="DEBUG"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        capsys.readouterr()
        await client.get("/api/v1/health/live")

    [access] = events(capsys.readouterr().out, "http.request")
    assert access["level"] == "debug"


async def test_unhandled_exception_is_logged_with_request_id_and_500(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    response = await client.get("/api/v1/_test/boom", headers={REQUEST_ID_HEADER: "req-boom"})
    output = capsys.readouterr().out

    assert response.status_code == 500
    [error] = events(output, "http.unhandled_exception")
    assert error["request_id"] == "req-boom"
    assert error["exception"][0]["exc_type"] == "RuntimeError"
    [access] = events(output, "http.request")
    assert access["status"] == 500
    assert access["level"] == "error"


async def test_concurrent_requests_do_not_mix_request_ids(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    # The slow request starts first and finishes last, overlapping the fast one.
    slow, fast = await asyncio.gather(
        client.get("/api/v1/_test/log?delay=0.2", headers={REQUEST_ID_HEADER: "slow"}),
        client.get("/api/v1/_test/log", headers={REQUEST_ID_HEADER: "fast"}),
    )
    output = capsys.readouterr().out

    assert (slow.headers[REQUEST_ID_HEADER], fast.headers[REQUEST_ID_HEADER]) == ("slow", "fast")
    assert sorted(line["request_id"] for line in events(output, "route.handled")) == [
        "fast",
        "slow",
    ]
    assert structlog.contextvars.get_contextvars() == {}

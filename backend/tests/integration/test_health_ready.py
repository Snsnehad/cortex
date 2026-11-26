"""Readiness against real infrastructure (TASK-01.06).

Requires the Docker Compose stack: ``docker compose -f infra/compose/docker-compose.yml up -d``.
Skip with ``pytest -m "not integration"``.
"""

import time

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def get_ready(settings: Settings) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/v1/health/ready")


@pytest.fixture
def settings() -> Settings:
    # Reads the root .env if present, exactly like the running app.
    return Settings()


async def test_ready_against_running_stack(settings: Settings) -> None:
    response = await get_ready(settings)

    body = response.json()
    assert response.status_code == 200, body
    assert {name: c["status"] for name, c in body["checks"].items()} == {
        "postgres": "ok",
        "redis": "ok",
        "qdrant": "ok",
    }


async def test_unreachable_infrastructure_returns_503_quickly(settings: Settings) -> None:
    # Port 1 on localhost refuses connections: a real network failure for every dependency.
    broken = settings.model_copy(
        update={
            "database": settings.database.model_copy(update={"port": 1}),
            "redis": settings.redis.model_copy(update={"url": SecretStr("redis://127.0.0.1:1/0")}),
            "qdrant": settings.qdrant.model_copy(update={"url": "http://127.0.0.1:1"}),
        }
    )

    started = time.perf_counter()
    response = await get_ready(broken)
    elapsed = time.perf_counter() - started

    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "unavailable"
    assert all(check["status"] in {"error", "timeout"} for check in body["checks"].values())
    assert elapsed < 5, "checks must be bounded by the timeout, not hang"

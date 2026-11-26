"""HTTP contract of the health endpoints, with fake checks (TASK-01.06)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.dependencies.health import get_health_service
from app.main import create_app
from app.services.health import HealthService

pytestmark = pytest.mark.anyio


class OkCheck:
    def __init__(self, name: str) -> None:
        self.name = name

    async def check(self) -> None:
        return None


class DownCheck:
    def __init__(self, name: str) -> None:
        self.name = name

    async def check(self) -> None:
        raise ConnectionRefusedError


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(_env_file=None))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def use_checks(app: FastAPI, *checks: OkCheck | DownCheck) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(list(checks))


async def test_live_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


async def test_live_does_not_depend_on_infrastructure(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_checks(app, DownCheck("postgres"), DownCheck("redis"))

    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200


async def test_ready_returns_200_when_all_dependencies_are_up(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_checks(app, OkCheck("postgres"), OkCheck("redis"), OkCheck("qdrant"))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"postgres", "redis", "qdrant"}
    assert all(check["status"] == "ok" for check in body["checks"].values())
    assert response.headers["cache-control"] == "no-store"


async def test_ready_returns_503_when_a_dependency_is_down(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_checks(app, OkCheck("postgres"), DownCheck("redis"), OkCheck("qdrant"))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["redis"] == {
        "status": "error",
        "latency_ms": body["checks"]["redis"]["latency_ms"],
        "error": "ConnectionRefusedError",
    }
    assert body["checks"]["postgres"]["status"] == "ok"


async def test_openapi_documents_versioned_health_routes(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths


def test_app_keeps_the_settings_it_was_built_with() -> None:
    settings = Settings(_env_file=None, log_level="DEBUG")

    assert create_app(settings).state.settings is settings

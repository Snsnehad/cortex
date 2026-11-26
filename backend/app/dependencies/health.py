"""Concrete readiness checks for Cortex's infrastructure and the HealthService factory.

Each check opens a short-lived connection on purpose: readiness must prove that a *new*
connection can be made right now, which a long-lived pooled connection would not show.
Probes run every few seconds, so the cost is negligible.
"""

from typing import Annotated

import httpx
import psycopg
import redis.asyncio as redis
from fastapi import Depends

from app.config import Settings
from app.dependencies.settings import SettingsDep
from app.services.health import DEFAULT_CHECK_TIMEOUT_SECONDS, HealthService


class PostgresCheck:
    name = "postgres"

    def __init__(self, settings: Settings, timeout_seconds: float) -> None:
        db = settings.database
        self._conninfo = psycopg.conninfo.make_conninfo(
            host=db.host,
            port=db.port,
            user=db.user,
            password=db.password.get_secret_value(),
            dbname=db.name,
            connect_timeout=max(1, int(timeout_seconds)),
            application_name="cortex-health",
        )

    async def check(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._conninfo) as conn:
            await conn.execute("SELECT 1")


class RedisCheck:
    name = "redis"

    def __init__(self, settings: Settings, timeout_seconds: float) -> None:
        self._url = settings.redis.url.get_secret_value()
        self._timeout_seconds = timeout_seconds

    async def check(self) -> None:
        client = redis.Redis.from_url(
            self._url,
            socket_connect_timeout=self._timeout_seconds,
            socket_timeout=self._timeout_seconds,
        )
        try:
            if not await client.ping():
                raise ConnectionError("redis PING did not return PONG")
        finally:
            await client.aclose()


class QdrantCheck:
    name = "qdrant"

    def __init__(self, settings: Settings, timeout_seconds: float) -> None:
        self._url = settings.qdrant.url.rstrip("/") + "/readyz"
        api_key = settings.qdrant.api_key
        self._headers = {"api-key": api_key.get_secret_value()} if api_key else {}
        self._timeout_seconds = timeout_seconds

    async def check(self) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(self._url, headers=self._headers)
            response.raise_for_status()


def build_health_service(
    settings: Settings, timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS
) -> HealthService:
    return HealthService(
        checks=[
            PostgresCheck(settings, timeout_seconds),
            RedisCheck(settings, timeout_seconds),
            QdrantCheck(settings, timeout_seconds),
        ],
        timeout_seconds=timeout_seconds,
    )


def get_health_service(settings: SettingsDep) -> HealthService:
    """FastAPI dependency. Tests override this with fake checks."""
    return build_health_service(settings)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]

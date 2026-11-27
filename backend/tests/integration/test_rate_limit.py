"""Rate limiting against a real Redis: the limiter itself, and the limits on the auth API.

Every test uses its own key prefix (``test_settings`` gives one per test), so counts never
leak between tests or between runs.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.rate_limit import RateLimiter, RateLimitPolicy, hash_identity
from app.main import create_app
from app.models import User

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery staple"
DEAD_REDIS_URL = SecretStr("redis://127.0.0.1:1/0")  # nothing listens on port 1


@pytest.fixture
async def redis_client(test_settings: Settings) -> AsyncIterator[redis.Redis]:
    client = redis.Redis.from_url(test_settings.redis.url.get_secret_value())
    yield client
    await client.aclose()


@pytest.fixture
def limiter(redis_client: redis.Redis) -> RateLimiter:
    return RateLimiter(redis_client, key_prefix=f"cortex-test:{uuid.uuid4().hex}")


# --- The limiter ----------------------------------------------------------------------------


async def test_allows_exactly_the_limit_then_denies_with_a_retry_time(
    limiter: RateLimiter,
) -> None:
    policy = RateLimitPolicy("t", limit=3, window_seconds=60)
    results = [await limiter.hit(policy, "client-a") for _ in range(5)]

    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert all(r.retry_after_seconds == 0 for r in results[:3])
    assert all(0 < r.retry_after_seconds <= 60 for r in results[3:])


async def test_identities_and_policies_are_counted_separately(limiter: RateLimiter) -> None:
    one = RateLimitPolicy("one", limit=1, window_seconds=60)
    two = RateLimitPolicy("two", limit=1, window_seconds=60)

    assert (await limiter.hit(one, "client-a")).allowed
    assert not (await limiter.hit(one, "client-a")).allowed
    assert (await limiter.hit(one, "client-b")).allowed  # other identity
    assert (await limiter.hit(two, "client-a")).allowed  # other policy


async def test_window_slides_and_rejections_do_not_extend_the_lockout(
    limiter: RateLimiter,
) -> None:
    policy = RateLimitPolicy("t", limit=2, window_seconds=2)
    assert (await limiter.hit(policy, "c")).allowed  # t = 0

    await asyncio.sleep(1.0)
    # t = 1: the second slot (this also keeps the key alive until t = 3) ...
    assert (await limiter.hit(policy, "c")).allowed
    for _ in range(5):  # ... then hammering while blocked, which must not be recorded
        denied = await limiter.hit(policy, "c")
        assert not denied.allowed
        assert denied.retry_after_seconds == 1  # the t = 0 entry leaves the window at t = 2

    await asyncio.sleep(1.1)

    # t = 2.1: the t = 0 request has left the window, so exactly one slot is free. Had the
    # t = 1 rejections been recorded, they would still be in the window and block this.
    assert (await limiter.hit(policy, "c")).allowed
    assert not (await limiter.hit(policy, "c")).allowed


async def test_concurrent_requests_cannot_exceed_the_limit(limiter: RateLimiter) -> None:
    policy = RateLimitPolicy("t", limit=5, window_seconds=60)
    results = await asyncio.gather(*(limiter.hit(policy, "c") for _ in range(30)))
    assert sum(r.allowed for r in results) == 5


async def test_keys_hold_no_raw_identity_and_expire(
    limiter: RateLimiter, redis_client: redis.Redis
) -> None:
    policy = RateLimitPolicy("t", limit=5, window_seconds=60)
    identity = "203.0.113.7"
    await limiter.hit(policy, identity)

    key = limiter.key_for(policy, identity)
    assert identity not in key
    assert key.endswith(hash_identity(identity))
    assert 0 < await redis_client.pttl(key) <= 60_000


# --- The auth API ---------------------------------------------------------------------------


def limited_settings(base: Settings, **limits: Any) -> Settings:
    return base.model_copy(update={"rate_limit": base.rate_limit.model_copy(update=limits)})


@pytest.fixture
async def make_client(
    db_engine: AsyncEngine,  # empties the tables after the test
) -> AsyncIterator[Any]:
    apps: list[FastAPI] = []
    clients: list[httpx.AsyncClient] = []

    async def make(settings: Settings) -> httpx.AsyncClient:
        app = create_app(settings)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        apps.append(app)
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()
    for app in apps:
        await app.state.db_engine.dispose()
        await app.state.redis.aclose()


async def register(client: httpx.AsyncClient, email: str) -> httpx.Response:
    body = {"email": email, "password": PASSWORD, "full_name": "Alice Smith"}
    return await client.post("/api/v1/auth/register", json=body)


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


def assert_rate_limited(response: httpx.Response) -> None:
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(response.headers["Retry-After"]) <= 3600


async def test_login_is_limited_per_ip(test_settings: Settings, make_client: Any) -> None:
    client = await make_client(limited_settings(test_settings, login_per_ip_per_minute=3))

    # Different (unknown) accounts: only the IP limit can apply.
    statuses = [(await login(client, f"user{i}@example.com")).status_code for i in range(3)]
    assert statuses == [401, 401, 401]
    assert_rate_limited(await login(client, "user9@example.com"))


async def test_login_is_limited_per_account_whatever_the_spelling(
    test_settings: Settings, make_client: Any
) -> None:
    client = await make_client(limited_settings(test_settings, login_per_email_per_minute=2))
    assert (await register(client, "alice@example.com")).status_code == 201

    assert (await login(client, "alice@example.com", "wrong password 1")).status_code == 401
    assert (await login(client, "  ALICE@Example.com ", "wrong password 2")).status_code == 401
    # Even the correct password is refused now: the account is locked for the window.
    assert_rate_limited(await login(client, "Alice@example.com"))
    # Other accounts are unaffected.
    assert (await login(client, "bob@example.com")).status_code == 401


async def test_registration_is_limited_per_ip(
    test_settings: Settings, make_client: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    client = await make_client(limited_settings(test_settings, register_per_ip_per_hour=2))

    assert (await register(client, "a1@example.com")).status_code == 201
    assert (await register(client, "a2@example.com")).status_code == 201
    assert_rate_limited(await register(client, "a3@example.com"))

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 2


async def test_auth_fails_closed_when_redis_is_down(
    test_settings: Settings, make_client: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    dead = test_settings.model_copy(
        update={"redis": test_settings.redis.model_copy(update={"url": DEAD_REDIS_URL})}
    )
    client = await make_client(dead)

    for response in (await register(client, "a@example.com"), await login(client, "a@x.com")):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_disabled_limiting_never_touches_redis(
    test_settings: Settings, make_client: Any
) -> None:
    settings = limited_settings(test_settings, enabled=False, login_per_ip_per_minute=1)
    settings = settings.model_copy(
        update={"redis": settings.redis.model_copy(update={"url": DEAD_REDIS_URL})}
    )
    client = await make_client(settings)

    assert (await register(client, "a@example.com")).status_code == 201
    for _ in range(3):
        assert (await login(client, "a@example.com")).status_code == 200


async def test_logs_never_contain_the_raw_identity(
    test_settings: Settings, make_client: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = limited_settings(test_settings, login_per_email_per_minute=1)
    client = await make_client(settings.model_copy(update={"log_format": "json"}))
    email = "secret-person@example.com"

    await login(client, email)
    assert_rate_limited(await login(client, email))

    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    exceeded = [e for e in events if e.get("event") == "rate_limit.exceeded"]
    assert len(exceeded) == 1
    assert exceeded[0]["policy"] == "login_email"
    assert exceeded[0]["identity_ref"] == hash_identity(email)[:12]
    assert "secret-person" not in output

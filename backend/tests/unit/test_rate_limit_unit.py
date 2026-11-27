"""Rate-limit building blocks that need no Redis: policies, identities and failure policy."""

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.config.settings import RateLimitSettings
from app.core.errors import RateLimitedError
from app.core.rate_limit import (
    RateLimitPolicy,
    RateLimitResult,
    RateLimitUnavailableError,
    hash_identity,
)
from app.dependencies.rate_limit import RateLimitGuard, normalize_email

pytestmark = pytest.mark.anyio


class StubLimiter:
    """Stands in for RateLimiter: returns a fixed result or raises a fixed error."""

    def __init__(self, result: RateLimitResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    async def hit(self, policy: RateLimitPolicy, identity: str) -> RateLimitResult:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def guard(limiter: StubLimiter, **limits: Any) -> RateLimitGuard:
    settings = Settings(_env_file=None, rate_limit=RateLimitSettings(**limits))
    return RateLimitGuard(limiter, settings)  # type: ignore[arg-type]


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (5, 0), (-1, 60)])
def test_policy_rejects_non_positive_values(limit: int, window: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimitPolicy("p", limit=limit, window_seconds=window)


def test_policies_come_from_settings() -> None:
    g = guard(
        StubLimiter(),
        login_per_ip_per_minute=7,
        login_per_email_per_minute=3,
        register_per_ip_per_hour=2,
    )
    assert (g.login_ip.limit, g.login_ip.window_seconds) == (7, 60)
    assert (g.login_email.limit, g.login_email.window_seconds) == (3, 60)
    assert (g.register_ip.limit, g.register_ip.window_seconds) == (2, 3600)


def test_identity_hash_is_stable_and_hides_the_value() -> None:
    assert hash_identity("alice@example.com") == hash_identity("alice@example.com")
    assert hash_identity("alice@example.com") != hash_identity("bob@example.com")
    assert "alice" not in hash_identity("alice@example.com")


def test_email_normalization() -> None:
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"


async def test_allowed_request_passes() -> None:
    stub = StubLimiter(RateLimitResult(allowed=True, remaining=4, retry_after_seconds=0))
    await guard(stub).check(RateLimitPolicy("p", 5, 60), "id")
    assert stub.calls == 1


async def test_denied_request_raises_429_with_retry_after() -> None:
    stub = StubLimiter(RateLimitResult(allowed=False, remaining=0, retry_after_seconds=42))
    with pytest.raises(RateLimitedError) as caught:
        await guard(stub).check(RateLimitPolicy("p", 5, 60), "id")
    assert caught.value.retry_after_seconds == 42


@pytest.mark.parametrize(
    "error", [RedisConnectionError("down"), TimeoutError(), OSError("refused")]
)
async def test_redis_failure_fails_closed_by_default(error: Exception) -> None:
    with pytest.raises(RateLimitUnavailableError) as caught:
        await guard(StubLimiter(error=error)).check(RateLimitPolicy("p", 5, 60), "id")
    assert caught.value.code == "RATE_LIMIT_UNAVAILABLE"


async def test_redis_failure_can_fail_open() -> None:
    stub = StubLimiter(error=RedisConnectionError("down"))
    await guard(stub).check(RateLimitPolicy("p", 5, 60), "id", fail_closed=False)
    assert stub.calls == 1


async def test_programming_errors_are_not_mistaken_for_an_outage() -> None:
    with pytest.raises(ZeroDivisionError):
        await guard(StubLimiter(error=ZeroDivisionError())).check(RateLimitPolicy("p", 5, 60), "id")


async def test_disabled_guard_never_calls_the_limiter() -> None:
    stub = StubLimiter(error=AssertionError("must not be called"))
    await guard(stub, enabled=False).check(RateLimitPolicy("p", 5, 60), "id")
    assert stub.calls == 0

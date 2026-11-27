"""Rate-limit dependencies for routes.

``RateLimitGuard`` (one per app, on ``app.state.rate_limits``) turns the configured limits into
named policies and enforces them:

* allowed → nothing happens;
* over the limit → 429 ``RATE_LIMITED`` with a ``Retry-After`` header;
* Redis unavailable → fail closed: 503 ``RATE_LIMIT_UNAVAILABLE`` (every policy today
  guards authentication; a fail-open policy can pass ``fail_closed=False``).

Routes use the IP dependencies (``LoginIpLimit``, ``RegisterIpLimit``); the per-email login
limit needs the parsed body, so the login route calls ``guard.check`` itself.

The client IP is ``request.client.host``, i.e. the TCP peer. Behind a load balancer that is
the balancer's address; trusting ``X-Forwarded-For`` from the right hops is configured with
the deployment (Phase 16), never by reading the header blindly here (clients can forge it).
"""

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.rate_limit import (
    REDIS_UNAVAILABLE_ERRORS,
    RateLimiter,
    RateLimitPolicy,
    RateLimitUnavailableError,
    hash_identity,
)

logger = get_logger("cortex.rate_limit")

UNKNOWN_CLIENT = "unknown"


class RateLimitGuard:
    def __init__(self, limiter: RateLimiter | None, settings: Settings) -> None:
        config = settings.rate_limit
        # None (limiting disabled, development/test only: deployed envs refuse to start).
        self._limiter = limiter if config.enabled else None
        self.login_ip = RateLimitPolicy("login_ip", config.login_per_ip_per_minute, 60)
        self.login_email = RateLimitPolicy("login_email", config.login_per_email_per_minute, 60)
        self.register_ip = RateLimitPolicy("register_ip", config.register_per_ip_per_hour, 3600)

    async def check(
        self, policy: RateLimitPolicy, identity: str, *, fail_closed: bool = True
    ) -> None:
        if self._limiter is None:
            return
        # Logs carry a short hash prefix, enough to correlate events but not the identity.
        identity_ref = hash_identity(identity)[:12]
        try:
            result = await self._limiter.hit(policy, identity)
        except REDIS_UNAVAILABLE_ERRORS as exc:
            logger.warning(
                "rate_limit.unavailable",
                policy=policy.name,
                fail_closed=fail_closed,
                error_type=type(exc).__name__,
            )
            if fail_closed:
                raise RateLimitUnavailableError from exc
            return
        if not result.allowed:
            logger.warning(
                "rate_limit.exceeded",
                policy=policy.name,
                identity_ref=identity_ref,
                retry_after_seconds=result.retry_after_seconds,
            )
            raise RateLimitedError(retry_after_seconds=result.retry_after_seconds)


def get_rate_limits(request: Request) -> RateLimitGuard:
    guard: RateLimitGuard = request.app.state.rate_limits
    return guard


RateLimitsDep = Annotated[RateLimitGuard, Depends(get_rate_limits)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else UNKNOWN_CLIENT


async def limit_login_by_ip(request: Request, guard: RateLimitsDep) -> None:
    await guard.check(guard.login_ip, client_ip(request))


async def limit_register_by_ip(request: Request, guard: RateLimitsDep) -> None:
    await guard.check(guard.register_ip, client_ip(request))


def normalize_email(email: str) -> str:
    """Case and surrounding spaces must not give an attacker extra attempts per account."""
    return email.strip().lower()

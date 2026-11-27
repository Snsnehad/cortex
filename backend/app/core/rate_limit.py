"""Redis sliding-window rate limiter.

Each limited identity (an IP, a hashed email, later an organization) has one Redis sorted set.
Every allowed request adds a member scored with its timestamp; members older than the window
are trimmed. A request is allowed while fewer than ``limit`` members remain.

The whole check-and-record runs as **one Lua script**, so it is atomic in Redis: twenty
simultaneous requests against a limit of five let exactly five through, never six. The clock
is Redis ``TIME``, so several API processes with skewed clocks still share one window.

Rejected requests are *not* recorded: a client that keeps hammering does not extend its own
lockout forever, it simply waits until its oldest allowed request leaves the window.

Keys contain a SHA-256 of the identity, never the raw IP or email (Redis is not a place for
personal data, and keys show up in ``MONITOR`` and slow logs).

Failure policy is chosen per call by the caller (see ``app.dependencies.rate_limit``):
authentication endpoints fail **closed** (503) when Redis is unavailable, because silently
dropping brute-force protection is worse than a short login outage.
"""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.errors import UpstreamUnavailableError

# KEYS[1] = the sorted set; ARGV = limit, window_ms, member suffix (unique per call).
# Returns {allowed (0/1), remaining, retry_after_ms}.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local t = redis.call('TIME')
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now_ms, now_ms .. '-' .. ARGV[3])
  redis.call('PEXPIRE', key, window_ms)
  return {1, limit - count - 1, 0}
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_ms = window_ms
if oldest[2] then
  retry_ms = tonumber(oldest[2]) + window_ms - now_ms
end
if retry_ms < 1 then
  retry_ms = 1
end
return {0, 0, retry_ms}
"""


class RateLimitUnavailableError(UpstreamUnavailableError):
    """Redis could not be reached, and this limit fails closed."""

    default_code = "RATE_LIMIT_UNAVAILABLE"
    default_message = "The service is temporarily unavailable. Please retry shortly."


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """A named limit: at most ``limit`` requests per ``window_seconds`` per identity."""

    name: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("rate limit and window must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int  # 0 when allowed


def hash_identity(identity: str) -> str:
    """Stable, non-reversible key part for an identity (IP, normalized email, ...)."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class RateLimiter:
    def __init__(self, client: redis.Redis, *, key_prefix: str) -> None:
        self._client = client
        self._key_prefix = key_prefix
        self._script: Any = client.register_script(_SLIDING_WINDOW_LUA)

    def key_for(self, policy: RateLimitPolicy, identity: str) -> str:
        return f"{self._key_prefix}:{policy.name}:{hash_identity(identity)}"

    async def hit(self, policy: RateLimitPolicy, identity: str) -> RateLimitResult:
        """Count one request; raises ``RedisError`` (or ``OSError``) if Redis is unreachable."""
        raw = await self._script(
            keys=[self.key_for(policy, identity)],
            args=[policy.limit, policy.window_seconds * 1000, uuid.uuid4().hex],
        )
        allowed, remaining, retry_after_ms = (int(value) for value in raw)
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=remaining,
            # Round up: telling a client to retry too early just earns it another 429.
            retry_after_seconds=-(-retry_after_ms // 1000),
        )


# Errors that mean "Redis is unavailable", as opposed to a bug in our code.
REDIS_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (RedisError, OSError, TimeoutError)

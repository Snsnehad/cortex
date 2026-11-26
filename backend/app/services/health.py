"""Dependency health checking.

``HealthService`` runs a set of :class:`DependencyCheck` objects concurrently, each bounded by
a timeout, and reports per-dependency status plus an overall verdict. It knows nothing about
PostgreSQL, Redis or Qdrant: concrete checks are injected (see ``app.dependencies.health``),
which keeps this logic unit-testable with fakes.
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

DEFAULT_CHECK_TIMEOUT_SECONDS = 2.0


class CheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class DependencyCheck(Protocol):
    """Something the application needs in order to serve traffic."""

    @property
    def name(self) -> str: ...

    async def check(self) -> None:
        """Return normally when healthy; raise any exception when not."""
        ...


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    latency_ms: float
    # Exception class name only. Messages can contain hosts, users or URLs, and this result
    # is served on an unauthenticated endpoint.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    checks: tuple[CheckResult, ...]

    @property
    def ready(self) -> bool:
        return all(result.status is CheckStatus.OK for result in self.checks)


class HealthService:
    def __init__(
        self,
        checks: Sequence[DependencyCheck],
        timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    ) -> None:
        names = [check.name for check in checks]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate health check names: {names}")
        self._checks = tuple(checks)
        self._timeout_seconds = timeout_seconds

    async def readiness(self) -> ReadinessReport:
        """Run every check concurrently; total time ~ the slowest check, never > timeout."""
        results = await asyncio.gather(*(self._run(check) for check in self._checks))
        return ReadinessReport(checks=tuple(results))

    async def _run(self, check: DependencyCheck) -> CheckResult:
        started = time.perf_counter()
        status, error = CheckStatus.OK, None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await check.check()
        except TimeoutError:
            status = CheckStatus.TIMEOUT
        # A health check must turn *any* failure of the dependency into a status instead of
        # crashing the probe, so a broad catch is intentional here.
        except Exception as exc:  # noqa: BLE001
            status, error = CheckStatus.ERROR, type(exc).__name__
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(name=check.name, status=status, latency_ms=latency_ms, error=error)

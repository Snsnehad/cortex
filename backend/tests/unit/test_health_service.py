"""HealthService behaviour with fake dependency checks (TASK-01.06)."""

import asyncio
import time

import pytest

from app.services.health import CheckStatus, HealthService

pytestmark = pytest.mark.anyio


class FakeCheck:
    def __init__(self, name: str, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.name = name
        self._delay = delay
        self._error = error
        self.calls = 0

    async def check(self) -> None:
        self.calls += 1
        await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error


async def test_ready_when_every_check_passes() -> None:
    report = await HealthService([FakeCheck("a"), FakeCheck("b")]).readiness()

    assert report.ready
    assert [(r.name, r.status) for r in report.checks] == [
        ("a", CheckStatus.OK),
        ("b", CheckStatus.OK),
    ]


async def test_one_failing_dependency_makes_the_service_unready() -> None:
    report = await HealthService(
        [FakeCheck("db"), FakeCheck("cache", error=ConnectionRefusedError())]
    ).readiness()

    assert not report.ready
    by_name = {r.name: r for r in report.checks}
    assert by_name["db"].status is CheckStatus.OK
    assert by_name["cache"].status is CheckStatus.ERROR
    assert by_name["cache"].error == "ConnectionRefusedError"


async def test_error_details_are_not_exposed() -> None:
    leaky = RuntimeError("connection to postgres://cortex:s3cret@10.0.0.5 failed")

    report = await HealthService([FakeCheck("db", error=leaky)]).readiness()

    assert report.checks[0].error == "RuntimeError"


async def test_slow_dependency_times_out_within_the_bound() -> None:
    service = HealthService([FakeCheck("slow", delay=5)], timeout_seconds=0.1)

    started = time.perf_counter()
    report = await service.readiness()
    elapsed = time.perf_counter() - started

    assert report.checks[0].status is CheckStatus.TIMEOUT
    assert elapsed < 1.0


async def test_checks_run_concurrently() -> None:
    checks = [FakeCheck(name, delay=0.2) for name in ("a", "b", "c")]

    started = time.perf_counter()
    report = await HealthService(checks, timeout_seconds=2).readiness()
    elapsed = time.perf_counter() - started

    assert report.ready
    assert elapsed < 0.45, "three 0.2 s checks should take ~0.2 s total, not 0.6 s"
    assert all(check.calls == 1 for check in checks)


async def test_latency_is_reported() -> None:
    report = await HealthService([FakeCheck("a", delay=0.05)]).readiness()

    assert report.checks[0].latency_ms >= 40


def test_duplicate_check_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        HealthService([FakeCheck("db"), FakeCheck("db")])

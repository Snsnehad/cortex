"""Backoff with full jitter, and when a job task re-enqueues itself (TASK-05.03)."""

import random
from typing import Any

import pytest

from app.config.settings import WorkerSettings
from app.workers import base
from app.workers.base import JobResult, Outcome, backoff_seconds, execute_job_task

SETTINGS = WorkerSettings(retry_backoff_base_seconds=10, retry_backoff_max_seconds=600)


@pytest.mark.parametrize(
    ("attempt", "ceiling"), [(1, 10), (2, 20), (3, 40), (6, 320), (7, 600), (30, 600)]
)
def test_backoff_stays_within_the_exponential_ceiling(attempt: int, ceiling: float) -> None:
    rng = random.Random(1234)
    delays = [backoff_seconds(attempt, SETTINGS, rng) for _ in range(2000)]

    assert all(0 <= d <= ceiling for d in delays)
    # Full jitter really spreads retries over the whole range (not clustered at the top).
    assert min(delays) < ceiling * 0.05
    assert max(delays) > ceiling * 0.95


def test_backoff_is_reproducible_with_a_seed() -> None:
    first = [backoff_seconds(3, SETTINGS, random.Random(7)) for _ in range(3)]
    second = [backoff_seconds(3, SETTINGS, random.Random(7)) for _ in range(3)]
    assert first == second


class FakeTask:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def apply_async(self, *, args: tuple[Any, ...], countdown: float) -> None:
        self.enqueued.append({"args": args, "countdown": countdown})


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (JobResult(Outcome.RETRY, retry_in_seconds=12.5, error_code="X"), [12.5]),
        (JobResult(Outcome.SUCCEEDED), []),
        (JobResult(Outcome.FAILED, error_code="X"), []),
        (JobResult(Outcome.SKIPPED), []),
        (JobResult(Outcome.LOST), []),
    ],
)
def test_only_a_retry_re_enqueues_the_task(
    monkeypatch: pytest.MonkeyPatch, result: JobResult, expected: list[float]
) -> None:
    monkeypatch.setattr(base, "run_async", lambda *args: result)
    task = FakeTask()
    job_id = "01900000-0000-7000-8000-000000000001"

    outcome = execute_job_task(task, job_id, work=None)  # type: ignore[arg-type]

    assert outcome == result.outcome.value
    assert [e["countdown"] for e in task.enqueued] == expected
    assert all(e["args"] == (job_id,) for e in task.enqueued)

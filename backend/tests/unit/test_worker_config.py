"""Celery configuration and the per-process worker runtime (TASK-05.01). No broker needed."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.config.settings import WorkerSettings
from app.workers import runtime
from app.workers.celery_app import create_celery
from app.workers.tasks import PING_TASK


def settings(**worker: object) -> Settings:
    return Settings(_env_file=None, worker=WorkerSettings(**worker))  # type: ignore[arg-type]


def test_reliability_settings() -> None:
    conf = create_celery(settings()).conf

    # At-least-once delivery: acknowledged on completion, requeued if the process dies.
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    # JSON only: never unpickle a message from the broker.
    assert conf.accept_content == ["json"]
    assert conf.task_serializer == "json"
    # Job state lives in PostgreSQL, not in a result backend.
    assert conf.task_ignore_result is True
    assert conf.get("result_backend") is None
    # Logging stays ours.
    assert conf.worker_hijack_root_logger is False
    assert conf.worker_redirect_stdouts is False
    assert conf.broker_connection_retry_on_startup is True


def test_limits_and_broker_come_from_settings() -> None:
    conf = create_celery(
        settings(
            broker_url=SecretStr("redis://broker.internal:6379/1"),
            concurrency=4,
            task_soft_time_limit_seconds=100,
            task_time_limit_seconds=120,
            visibility_timeout_seconds=600,
        )
    ).conf

    assert conf.broker_url == "redis://broker.internal:6379/1"
    assert conf.worker_concurrency == 4
    assert (conf.task_soft_time_limit, conf.task_time_limit) == (100, 120)
    assert conf.broker_transport_options == {"visibility_timeout": 600}


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"task_soft_time_limit_seconds": 60, "task_time_limit_seconds": 60}, "soft limit"),
        (
            {
                "task_soft_time_limit_seconds": 60,
                "task_time_limit_seconds": 120,
                "visibility_timeout_seconds": 120,
            },
            "visibility_timeout",
        ),
    ],
)
def test_inconsistent_limits_are_rejected(limits: dict[str, int], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkerSettings(**limits)  # type: ignore[arg-type]


def test_the_broker_url_is_a_secret() -> None:
    worker = WorkerSettings(broker_url=SecretStr("redis://:hunter2@redis:6379/1"))
    assert "hunter2" not in repr(worker)


def test_ping_task_is_registered() -> None:
    app = create_celery(settings())
    app.loader.import_default_modules()
    assert PING_TASK in app.tasks


# --- per-process runtime ------------------------------------------------------------------


@pytest.fixture
def clean_runtime() -> Iterator[None]:
    runtime.configure(Settings(_env_file=None))
    yield
    for context in list(_created):
        context.runner.close()
    _created.clear()
    runtime._context = None


_created: list[runtime.WorkerContext] = []


@pytest.mark.usefixtures("clean_runtime")
def test_one_context_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.workers.runtime.os.getpid", lambda: 1000)
    first = runtime.get_context()
    _created.append(first)

    assert runtime.get_context() is first  # reused for every task in this process


@pytest.mark.usefixtures("clean_runtime")
def test_a_forked_child_never_reuses_its_parents_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.workers.runtime.os.getpid", lambda: 1000)
    parent = runtime.get_context()
    _created.append(parent)

    monkeypatch.setattr("app.workers.runtime.os.getpid", lambda: 2000)  # as after fork()
    child = runtime.get_context()
    _created.append(child)

    assert child is not parent
    assert child.engine is not parent.engine
    assert child.runner is not parent.runner


@pytest.mark.usefixtures("clean_runtime")
def test_shutdown_leaves_a_parents_context_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.workers.runtime.os.getpid", lambda: 1000)
    parent = runtime.get_context()
    _created.append(parent)
    monkeypatch.setattr("app.workers.runtime.os.getpid", lambda: 2000)

    runtime.shutdown()  # in the child: must not dispose the parent's engine or loop

    assert not parent.runner.get_loop().is_closed()


def test_the_reconciler_is_scheduled_and_its_ticks_expire() -> None:
    schedule = create_celery(settings(reconcile_interval_seconds=45)).conf.beat_schedule

    entry = schedule["reconcile-jobs"]
    assert entry["task"] == "cortex.reconcile_jobs"
    assert entry["schedule"] == 45.0
    # A tick nobody consumes (no worker running) expires instead of piling up in Redis.
    assert entry["options"]["expires"] == 45.0


def test_retrying_jobs_are_not_considered_lost_before_the_longest_backoff() -> None:
    with pytest.raises(ValidationError, match="stale_retrying_after_seconds"):
        WorkerSettings(retry_backoff_max_seconds=900, stale_retrying_after_seconds=900)

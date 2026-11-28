"""The worker end to end (TASK-05.01): the ping task against the real database, connection
reuse across tasks, JSON logs with the task id, and a real worker consuming from Redis.

Tests here are synchronous on purpose: a task runs on the worker process's own event loop
(app.workers.runtime), exactly as in production, not inside pytest's loop.
"""

import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
import redis
from celery import Celery
from celery.contrib.testing.worker import start_worker
from sqlalchemy.pool import QueuePool

from app.config import Settings
from app.core.logging import configure_logging
from app.workers import runtime
from app.workers.celery_app import create_celery
from app.workers.tasks import PING_TASK


@pytest.fixture
def worker_app(test_settings: Settings) -> Iterator[Celery]:
    app = create_celery(test_settings)
    app.loader.import_default_modules()
    yield app
    runtime.shutdown()


def run_eagerly(app: Celery, name: str) -> Any:
    return app.tasks[name].apply().get(timeout=10)


def test_ping_reaches_the_database(worker_app: Celery) -> None:
    assert run_eagerly(worker_app, PING_TASK) == "pong"


def test_tasks_reuse_one_loop_and_one_pool(worker_app: Celery) -> None:
    """With asyncio.run() per task, the second task would get a pooled connection that
    belongs to the first task's (closed) loop and fail."""
    for _ in range(3):
        assert run_eagerly(worker_app, PING_TASK) == "pong"
    context = runtime.get_context()
    pool = cast(QueuePool, context.engine.pool)
    assert pool.checkedin() >= 1  # the connection went back to the pool


def test_task_logs_are_json_and_carry_the_task_id(
    worker_app: Celery, test_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(test_settings.model_copy(update={"log_format": "json"}))
    capsys.readouterr()

    result = worker_app.tasks[PING_TASK].apply()

    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    by_name = {e["event"]: e for e in events}
    for name in ("task.started", "worker.ping", "task.finished"):
        assert by_name[name]["task_id"] == result.id, name
        assert by_name[name]["task_name"] == PING_TASK
    assert by_name["task.finished"]["state"] == "SUCCESS"
    assert by_name["task.finished"]["duration_ms"] >= 0


def test_a_real_worker_consumes_from_redis(test_settings: Settings) -> None:
    """Producer → Redis → worker thread → task, on a queue of its own."""
    queue = f"cortex-test-{uuid.uuid4().hex}"
    app = create_celery(test_settings)
    app.conf.task_default_queue = queue
    done = threading.Event()
    received: list[str] = []

    @app.task(name="cortex.test.record")
    def record(value: str) -> None:
        received.append(value)
        done.set()

    broker = redis.Redis.from_url(test_settings.worker.broker_url.get_secret_value())
    try:
        with start_worker(app, pool="solo", perform_ping_check=False, queues=[queue]):
            record.delay("hello from the producer")
            assert done.wait(timeout=20), "the worker never ran the task"
    finally:
        # The queue and Celery's routing keys for it: nothing may pile up in Redis.
        for key in broker.scan_iter(match=f"*{queue}*"):
            broker.delete(key)
        broker.close()
        runtime.shutdown()

    assert received == ["hello from the producer"]


def test_the_task_id_does_not_leak_into_later_log_lines(
    worker_app: Celery, test_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(test_settings.model_copy(update={"log_format": "json"}))
    worker_app.tasks[PING_TASK].apply()
    capsys.readouterr()

    runtime.logger.info("worker.idle")  # e.g. the worker loop between tasks

    line = next(
        json.loads(text)
        for text in capsys.readouterr().out.splitlines()
        if text.startswith("{") and '"worker.idle"' in text
    )
    assert "task_id" not in line
    assert "task_name" not in line

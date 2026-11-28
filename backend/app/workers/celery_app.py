"""The Celery application (ADR-001: worker and beat are separate processes of one codebase).

Start a worker (Linux containers only, ADR-015)::

    celery -A app.workers.celery_app worker --loglevel=INFO

Reliability choices (they make delivery **at-least-once**, so every task must be idempotent):

* ``task_acks_late``: a task is acknowledged when it *finishes*, not when it is received. If
  the worker dies mid-task, the broker delivers the task again instead of losing it.
* ``task_reject_on_worker_lost``: if the worker *process* is killed (OOM, SIGKILL), the task
  is requeued rather than marked failed.
* ``worker_prefetch_multiplier = 1``: a worker reserves one task at a time, so long ingestion
  tasks do not sit behind each other in one worker while another is idle.
* Redis ``visibility_timeout`` > hard time limit: otherwise Redis would hand a slow but
  healthy task to a second worker (enforced by ``WorkerSettings``).
* Soft/hard time limits: ``SoftTimeLimitExceeded`` lets a task record its failure; the hard
  limit kills a task that ignores it.
* JSON only (``accept_content=["json"]``): a pickle message from anyone who can write to
  Redis would mean remote code execution.
* No result backend: job state lives in PostgreSQL (``processing_jobs``), the source of truth.

Logging: Celery does not configure logging (``setup_logging`` is handled here), so worker
output is the same structured JSON as the API, with ``task_id`` and ``task_name`` bound to
every line a task emits.
"""

import time
from typing import Any

import structlog
from celery import Celery, signals

from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.workers import runtime

logger = get_logger("cortex.worker")

DEFAULT_QUEUE = "cortex"
TASK_MODULES = ["app.workers.tasks"]


def create_celery(settings: Settings) -> Celery:
    worker = settings.worker
    app = Celery("cortex", broker=worker.broker_url.get_secret_value(), include=TASK_MODULES)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_concurrency=worker.concurrency,
        task_soft_time_limit=worker.task_soft_time_limit_seconds,
        task_time_limit=worker.task_time_limit_seconds,
        broker_transport_options={"visibility_timeout": worker.visibility_timeout_seconds},
        broker_connection_retry_on_startup=True,
        task_ignore_result=True,
        result_backend=None,
        task_default_queue=DEFAULT_QUEUE,
        enable_utc=True,
        timezone="UTC",
        # Logging is ours (structlog JSON); Celery must not replace handlers or stdout.
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
        worker_send_task_events=False,
        # Beat: the reconciler. Each tick's message expires after one interval, so ticks
        # never pile up in Redis while no worker is running.
        beat_schedule={
            "reconcile-jobs": {
                "task": "cortex.reconcile_jobs",
                "schedule": float(worker.reconcile_interval_seconds),
                "options": {"expires": float(worker.reconcile_interval_seconds)},
            }
        },
    )
    runtime.configure(settings)
    return app


# --- signals: logging and the per-process runtime ---------------------------------------------


@signals.setup_logging.connect
def _setup_logging(**_: Any) -> None:
    # Connecting to this signal stops Celery from configuring logging itself.
    configure_logging(runtime.current_settings())


_started_at: dict[str, float] = {}


@signals.task_prerun.connect
def _task_started(task_id: str, task: Any, **_: Any) -> None:
    structlog.contextvars.bind_contextvars(task_id=task_id, task_name=task.name)
    _started_at[task_id] = time.perf_counter()
    logger.info("task.started")


@signals.task_postrun.connect
def _task_finished(task_id: str, state: str | None = None, **_: Any) -> None:
    started = _started_at.pop(task_id, None)
    duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
    logger.info("task.finished", state=state, duration_ms=duration_ms)
    structlog.contextvars.unbind_contextvars("task_id", "task_name")


@signals.worker_process_shutdown.connect
def _process_shutdown(**_: Any) -> None:
    runtime.shutdown()


@signals.worker_shutdown.connect
def _worker_shutdown(**_: Any) -> None:
    runtime.shutdown()  # solo/threads pools have no child processes


celery_app = create_celery(get_settings())

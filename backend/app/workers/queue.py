"""Enqueueing jobs from the API (TASK-05.04).

Rules:

* **Only after the commit.** A message must never reach a worker before the job row it
  names exists; the upload route enqueues after ``DocumentService.add`` has committed.
* **Never fails the request.** If the broker is down or slow, the upload still answers
  202: the job stays QUEUED and the reconciler re-enqueues it (``job.enqueue_failed`` is
  logged). Publishing is bounded by ``worker.enqueue_timeout_seconds``.
* Publishing is blocking I/O: call ``enqueue_ingest`` from a worker thread in async code.

The API's Celery app is a *producer* only. Its short socket timeouts must not be used by
workers, whose blocking queue reads need longer ones.
"""

import uuid
from typing import Protocol

from celery import Celery
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from app.config import Settings
from app.core.logging import get_logger
from app.models import JobType

logger = get_logger("cortex.queue")

INGEST_TASK = "cortex.ingest_document"
DELETE_TASK = "cortex.delete_document"
RECONCILE_TASK = "cortex.reconcile_jobs"
# Which task processes which kind of job (REINDEX arrives with its task).
TASK_FOR_JOB_TYPE: dict[JobType, str] = {
    JobType.INGEST: INGEST_TASK,
    JobType.DELETE: DELETE_TASK,
}
_BROKER_ERRORS = (OperationalError, RedisError, OSError, TimeoutError)


class JobQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID, job_type: JobType) -> bool:
        """True if the message reached the broker; False (logged) if not."""
        ...

    def enqueue_ingest(self, job_id: uuid.UUID) -> bool: ...

    def close(self) -> None: ...


def create_producer(settings: Settings) -> Celery:
    from app.workers.celery_app import create_celery  # celery_app imports this module

    app = create_celery(settings)
    timeout = settings.worker.enqueue_timeout_seconds
    app.conf.update(
        broker_connection_timeout=timeout,
        broker_connection_retry=False,
        broker_connection_max_retries=0,
        broker_transport_options={
            **app.conf.broker_transport_options,
            "socket_connect_timeout": timeout,
            "socket_timeout": timeout,
        },
    )
    return app


class CeleryJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def enqueue(self, job_id: uuid.UUID, job_type: JobType) -> bool:
        task = TASK_FOR_JOB_TYPE.get(job_type)
        if task is None:
            logger.warning("job.no_task_for_type", job_id=str(job_id), job_type=job_type.value)
            return False
        try:
            # retry=False: one attempt; the reconciler is the retry mechanism.
            self._app.send_task(task, args=(str(job_id),), retry=False)
        except _BROKER_ERRORS as exc:
            logger.warning("job.enqueue_failed", job_id=str(job_id), error_type=type(exc).__name__)
            return False
        logger.info("job.enqueued", job_id=str(job_id), task=task)
        return True

    def enqueue_ingest(self, job_id: uuid.UUID) -> bool:
        return self.enqueue(job_id, JobType.INGEST)

    def close(self) -> None:
        self._app.close()

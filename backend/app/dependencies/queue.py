"""The background job queue as a FastAPI dependency (``app.state.job_queue``)."""

from typing import Annotated

from fastapi import Depends, Request

from app.workers.queue import JobQueue


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]

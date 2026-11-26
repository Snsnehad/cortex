"""Liveness and readiness probes (no authentication).

* ``/health/live``  — the process is up and serving HTTP. Never touches dependencies, so a
  database outage does not make an orchestrator restart healthy API containers.
* ``/health/ready`` — PostgreSQL, Redis and Qdrant are reachable. Returns 503 otherwise, so a
  load balancer stops routing traffic to this instance until they recover.
"""

from fastapi import APIRouter, Response, status

from app.dependencies.health import HealthServiceDep
from app.schemas.health import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])

NO_STORE = "no-store"


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live(response: Response) -> LivenessResponse:
    response.headers["Cache-Control"] = NO_STORE
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(response: Response, health: HealthServiceDep) -> ReadinessResponse:
    report = await health.readiness()
    response.headers["Cache-Control"] = NO_STORE
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse.from_report(report)

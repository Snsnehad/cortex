"""Response models for the health endpoints."""

from typing import Literal

from pydantic import BaseModel

from app.services.health import CheckStatus, ReadinessReport


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class DependencyStatus(BaseModel):
    status: CheckStatus
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, DependencyStatus]

    @classmethod
    def from_report(cls, report: ReadinessReport) -> "ReadinessResponse":
        return cls(
            status="ok" if report.ready else "unavailable",
            checks={
                result.name: DependencyStatus(
                    status=result.status, latency_ms=result.latency_ms, error=result.error
                )
                for result in report.checks
            },
        )

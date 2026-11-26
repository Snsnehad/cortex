"""Application factory.

Run locally from ``backend/``::

    uv run uvicorn app.main:create_app --factory --reload --port 8000

A factory (instead of a module-level ``app``) means importing this module has no side effects:
settings are loaded when the app is built, and tests build isolated apps with their own
Settings.
"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.middleware.request_context import RequestContextMiddleware

API_V1_PREFIX = "/api/v1"
APP_VERSION = "0.1.0"
HEALTH_PATHS = (f"{API_V1_PREFIX}/health/live", f"{API_V1_PREFIX}/health/ready")

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Cortex API",
        version=APP_VERSION,
        summary="Multi-tenant AI knowledge & research engine",
        debug=settings.debug,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.include_router(api_router, prefix=API_V1_PREFIX)
    app.add_middleware(RequestContextMiddleware, quiet_paths=HEALTH_PATHS)

    logger.info(
        "app.created",
        environment=settings.environment.value,
        version=APP_VERSION,
        log_format=settings.log_format,
    )
    return app

"""Application factory.

Run locally from ``backend/``::

    uv run uvicorn app.main:create_app --factory --reload --port 8000

A factory (instead of a module-level ``app``) means importing this module has no side effects:
settings are loaded when the app is built, and tests build isolated apps with their own
Settings.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.database.session import create_engine, create_session_factory
from app.middleware.request_context import RequestContextMiddleware

API_V1_PREFIX = "/api/v1"
APP_VERSION = "0.1.0"
HEALTH_PATHS = (f"{API_V1_PREFIX}/health/live", f"{API_V1_PREFIX}/health/ready")

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Close every pooled connection on shutdown so the database sees a clean disconnect
    # instead of connections dropped mid-session.
    await app.state.db_engine.dispose()
    logger.info("app.shutdown", db_pool="disposed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        lifespan=lifespan,
        title="Cortex API",
        version=APP_VERSION,
        summary="Multi-tenant AI knowledge & research engine",
        debug=settings.debug,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    # Creating the engine opens no connection; the pool fills on first use.
    app.state.db_engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.db_engine)
    app.include_router(api_router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware, quiet_paths=HEALTH_PATHS)

    logger.info(
        "app.created",
        environment=settings.environment.value,
        version=APP_VERSION,
        log_format=settings.log_format,
    )
    return app

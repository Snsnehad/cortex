# syntax=docker/dockerfile:1
#
# Cortex backend image (TASK-01.05). One image serves the API now and the Celery worker /
# beat later (same code, different command).
#
#   docker build -t cortex-backend backend/
#
# Stage 1 resolves and installs dependencies with uv; stage 2 copies only the finished
# virtualenv and the application code into a clean base. Build tools, the uv binary, caches
# and dev dependencies (pytest, ruff, mypy) never reach the runtime image.

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

# --------------------------------------------------------------------------------------
# Stage 1: build the virtualenv
# --------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

# Same uv version as local development (ADR-003), copied from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock change,
# not on every code edit. --frozen fails the build if uv.lock is out of date.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --------------------------------------------------------------------------------------
# Stage 2: runtime
# --------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Fixed, non-root identity (uid > 10000 avoids clashing with host users on bind mounts).
RUN groupadd --system --gid 10001 cortex \
 && useradd --system --uid 10001 --gid cortex --no-create-home --shell /usr/sbin/nologin cortex \
 && mkdir -p /data/storage \
 && chown -R cortex:cortex /data

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CORTEX_STORAGE__LOCAL_ROOT=/data/storage

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# Code is owned by root and only readable by the app user: a compromised process cannot
# modify the application it is running.
COPY app ./app
# Migrations ship in the same image, so a deployment runs exactly the schema changes that
# match its code:  docker run <image> alembic upgrade head
COPY alembic.ini ./
COPY migrations ./migrations

USER cortex:cortex
EXPOSE 8000

# Liveness, not readiness: Docker/ECS restart unhealthy containers, and restarting the API
# cannot fix a database outage. Readiness (/health/ready) is for load balancers.
# Python's stdlib is used because the slim image has no curl.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2).status == 200 else 1)"]

# One process per container; scale by running more containers.
# --loop asyncio (not uvloop): during a dependency outage uvloop logs "Future exception was
# never retrieved" for every cancelled DNS lookup, flooding logs on each readiness probe.
# The standard loop behaves cleanly and matches tests and Windows dev (ADR-019).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio", "--no-server-header"]

# Development docs

Local setup details, dev scripts (TASK-01.08), worker operations, troubleshooting.

## Backend tooling (TASK-01.02)

All Python work goes through [uv](https://docs.astral.sh/uv/); you never need a system Python. Run from `backend/`:

| Command | What it does |
|---|---|
| `uv sync` | Create/update `backend/.venv` exactly as pinned in `uv.lock` (Python 3.12 is installed by uv if missing) |
| `uv run ruff check` | Lint (pycodestyle, pyflakes, bugbear, bandit security rules, import order, …) |
| `uv run ruff check --fix` | Lint and apply safe auto-fixes |
| `uv run ruff format` | Format code (`--check` to verify only, as CI does) |
| `uv run mypy` | Strict type check of `app/` and `tests/` |
| `uv run pytest` | Run the test suite |
| `uv run pytest -m "not integration"` | Skip tests that need Docker services |
| `uv add <pkg>` / `uv add --dev <pkg>` | Add a runtime / dev dependency (updates `pyproject.toml` and `uv.lock`; commit both) |

All tool configuration lives in `backend/pyproject.toml`.

### Conventions enforced by tooling
- Python is pinned to 3.12 (`requires-python`, `.python-version`, and a test).
- Every directory under `app/` must be a regular package with a docstring stating its responsibility (`tests/unit/test_project_layout.py`).
- Warnings are errors in tests (`filterwarnings = ["error"]`), so deprecations are fixed when they first appear.
- Test markers are strict: `integration`, `slow`, `eval`, `llm`. An unknown marker fails the run.

### Windows notes
- uv's cache is on C: and the project is on D:, so `link-mode = "copy"` is set in `pyproject.toml` (hardlinks cannot cross drives).

## Configuration (TASK-01.03)

Settings live in `backend/app/config/settings.py`; use them via `from app.config import get_settings`.

- Every setting is a `CORTEX_*` environment variable; nested groups use `__`, e.g. `CORTEX_DATABASE__PASSWORD`. Lists are JSON: `CORTEX_CORS_ORIGINS=["http://localhost:5173"]`.
- Local development: `Copy-Item .env.example .env` at the repo root. Real environment variables override `.env`.
- **Unknown `CORTEX_*` variables are an error** (typo protection), from the environment or `.env`.
- `staging` / `production` refuse to start with development defaults. The error lists every problem at once.
- Secrets are `SecretStr`: they print as `**********`. Call `.get_secret_value()` only where the raw value is needed.
- **Adding a setting:** add the field to the right group, document it in `.env.example` (commented `# CORTEX_...=` if optional), and add validation tests. `test_env_example_documents_every_setting_and_nothing_else` fails until `.env.example` matches.

Generate a real secret for any shared environment:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Local infrastructure (TASK-01.04)

`infra/compose/docker-compose.yml` runs PostgreSQL, Redis and Qdrant. Run commands from the repo root.

| Service | Image (pinned) | Host port (127.0.0.1 only) | Health check |
|---|---|---|---|
| PostgreSQL | `postgres:17.12-alpine` | 15432 → 5432 | `pg_isready` over TCP |
| Redis | `redis:7.4.12-alpine` | 16379 → 6379 | `redis-cli ping` |
| Qdrant | `qdrant/qdrant:v1.20.2-unprivileged` (non-root) | 16333 → 6333 (HTTP), 16334 → 6334 (gRPC) | HTTP 200 from `/readyz` |

```powershell
docker compose -f infra/compose/docker-compose.yml up -d --wait     # start, wait until healthy
docker compose -f infra/compose/docker-compose.yml ps               # status
docker compose -f infra/compose/docker-compose.yml logs -f qdrant   # follow logs
docker compose -f infra/compose/docker-compose.yml down             # stop, KEEP data
```

Add `--env-file .env` once you have a root `.env`, so the containers use the same credentials and Postgres port as the backend.

**Why ports 15432 / 16379 / 16333:** other local projects already use the defaults (5432, 6379, 6333) on this machine. Inside Docker the services keep their standard ports. The backend defaults and `.env.example` point at these host ports.

**Configuration choices**
- Postgres preloads `pg_stat_statements` (query statistics for TASK-14.06) and logs statements slower than 500 ms.
- Redis uses append-only persistence and `noeviction`, so queued jobs are never silently dropped when memory is full.
- Qdrant telemetry is disabled. Its image has no curl/wget, so the health check speaks HTTP through bash's `/dev/tcp`.
- Data lives in named volumes `cortex_postgres_data`, `cortex_redis_data`, `cortex_qdrant_data`.

**Destructive (deletes all local data; ask first / confirm):**
```powershell
docker compose -f infra/compose/docker-compose.yml down -v
```

**Troubleshooting:** `ports are not available … bind` means another process holds the port. Find it with `netstat -ano | Select-String ":16379\s"`, or change the Postgres host port with `CORTEX_DATABASE__PORT` in `.env`.

## Running the API (TASK-01.06)

From `backend/`, with the compose stack running:

```powershell
uv run uvicorn app.main:create_app --factory --reload --port 8000
```

| URL | Purpose |
|---|---|
| http://127.0.0.1:8000/api/v1/docs | Swagger UI |
| http://127.0.0.1:8000/api/v1/openapi.json | OpenAPI contract |
| http://127.0.0.1:8000/api/v1/health/live | Liveness: always 200 while the process serves HTTP; never checks dependencies |
| http://127.0.0.1:8000/api/v1/health/ready | Readiness: 200 only if PostgreSQL, Redis and Qdrant answer, else 503 with per-dependency status |

How readiness works: `app/services/health.py` runs the checks concurrently, each bounded by a 2 s timeout, so a dead dependency cannot hang the probe. The concrete checks are in `app/dependencies/health.py`. Each opens a fresh connection, to prove new connections work right now. Error responses carry only the exception *class name*: messages can contain hosts or credentials, and this endpoint is unauthenticated.

### Windows-specific gotchas (ADR-018)
1. **Use `127.0.0.1`, not `localhost`.** Async clients on Windows try IPv6 `::1` first and lose ~2 s per connection before falling back to IPv4 (measured: Postgres 10 s / Redis 2 s via `localhost`, versus 24 ms / 11 ms via `127.0.0.1`). The defaults and `.env.example` already use `127.0.0.1`.
2. **psycopg async needs the selector event loop.** Windows defaults to the Proactor loop, where psycopg raises `InterfaceError`. `--reload` makes uvicorn use the selector loop automatically. Without `--reload`, pass `--loop asyncio:SelectorEventLoop`. Tests handle this in `tests/conftest.py`. Linux (Docker, CI) is unaffected.
3. **Stopping a background uvicorn:** the process is `python.exe` running `...\Scripts\uvicorn.exe`. Stop it by port owner:
   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }
   ```

### Tests
- `uv run pytest -m "not integration"` runs without Docker (health logic is tested with fake checks).
- `tests/integration/test_health_ready.py` needs the compose stack. It checks the ready path and a real failure path (closed ports must return 503 within the timeout).

## API container (TASK-01.05)

`backend/Dockerfile` builds one image for the API (and later the Celery worker and beat). It is a two-stage build:
1. **builder**: uv 0.13.6 installs the locked runtime dependencies (`uv sync --frozen --no-dev`) into `/opt/venv`. This layer only rebuilds when `pyproject.toml` or `uv.lock` change (cold build ~70 s, code-only rebuild ~13 s).
2. **runtime**: `python:3.12.16-slim-trixie` + the venv + `app/`. No uv, no pytest/ruff/mypy, no tests, no `.env`.

```powershell
# Build and run the containerised API alongside the infrastructure (opt-in "app" profile)
docker compose -f infra/compose/docker-compose.yml --profile app up -d --build --wait
curl.exe http://127.0.0.1:18000/api/v1/health/ready
docker compose -f infra/compose/docker-compose.yml --profile app stop api
```

Inside the compose network the API reaches `postgres:5432`, `redis:6379` and `qdrant:6333` by service name. Compose overrides the host-oriented `127.0.0.1:1xxxx` values for the container.

**Hardening (verified):**

| Control | Result |
|---|---|
| Non-root user | Runs as `cortex` (uid 10001) |
| Read-only root filesystem | `/app` and `/opt/venv` can't be modified; only `/data/storage` (volume) and `/tmp` (tmpfs) are writable |
| Linux capabilities | All dropped (`CapEff` = 0), plus `no-new-privileges` |
| Server banner | Hidden (`--no-server-header`) |
| Network exposure | Port bound to 127.0.0.1:18000 only |

**Health check = liveness.** The Dockerfile `HEALTHCHECK` calls `/health/live`, not `/ready`. Orchestrators restart unhealthy containers, and a restart cannot fix a database outage. Verified: with Redis stopped, `/ready` returned 503 for 25 s while the container stayed `healthy` with 0 restarts. Readiness is for load balancers.

**Event loop = asyncio (ADR-019).** uvloop logged "Future exception was never retrieved" on every probe during a dependency outage (cancelled DNS lookups). Measured in the same outage: uvloop 4 spurious log lines, asyncio 0, identical health results.

## Logging (TASK-01.07)

All output is **one JSON object per line on stdout** (`CORTEX_LOG_FORMAT=json`, the default). Set `CORTEX_LOG_FORMAT=console` in your `.env` for coloured, human-readable output locally.

```python
from app.core.logging import get_logger

logger = get_logger(__name__)
logger.info("document.uploaded", document_id=str(doc.id), size_bytes=size)  # event name + fields
```

- **Event names, not sentences.** Use `area.action` names (`http.request`, `document.uploaded`) plus key/value fields. They are easy to search and aggregate.
- **One pipeline:** structlog events and standard-library loggers (uvicorn, httpx, later Celery and SQLAlchemy) are rendered identically. uvicorn's own access log is disabled; `cortex.access` replaces it.
- **Request id:** `RequestContextMiddleware` accepts a safe `X-Request-ID` (`[A-Za-z0-9._-]`, at most 128 chars) or generates one, returns it in the response header, and binds it so **every** log line written during that request carries `request_id`. It uses context variables, so concurrent requests never mix ids (tested).
- **Access log** (`cortex.access` / `http.request`): method, path, status, duration_ms, client_ip. **The query string is never logged.** Health probes log at DEBUG; 5xx responses log at ERROR.
- **Unhandled exceptions** are logged once, inside the request context (`http.unhandled_exception` with a structured traceback and the `request_id`).
- **Redaction (defence in depth; still never log secrets on purpose):**
  - Keys: exact `token`, `jwt`, `dsn`, `auth`, `cookie`, `set_cookie`, or ending in `password`, `passwd`, `secret`, `_token`, `api_key`, `apikey`, `authorization`, `credentials`, `private_key`, `_cookie`. Matching is recursive, case-insensitive, and treats `-` as `_`. Deliberately **not** a substring match, so `prompt_tokens` and `token_count` stay visible.
  - Text: `Bearer <token>` and `scheme://user:password@` in any string value, including exception messages in tracebacks.
- Never log document text, prompts or answers in full (lengths and ids only).

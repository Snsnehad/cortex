# Development docs

Local setup details, dev scripts (TASK-01.08), worker operations, troubleshooting.

## Developer commands (TASK-01.08)

`scripts\dev.cmd <command>` works from any folder and under any PowerShell execution policy.

| Command | What it does |
|---|---|
| `help` | List commands |
| `up` | Start PostgreSQL, Redis, Qdrant; wait until healthy; print ports |
| `up -App` | Same, plus build and start the API container on http://127.0.0.1:18000 |
| `down` | Stop all Cortex containers, including the API. **Data is kept** |
| `ps` | Container status |
| `logs [service] [-Follow]` | Last 200 log lines (`postgres`, `redis`, `qdrant`, `api`) |
| `api [-Port 8000]` | Run the API on the host with auto-reload |
| `test [pytest args]` | Backend tests. Arguments go to pytest unchanged: `test -m "not integration"`, `test -k settings -x` |
| `lint` | `ruff check`, `ruff format --check`, `mypy` |
| `format` | `ruff format` + `ruff check --fix` |
| `check` | `lint`, then `test`: what CI runs |
| `migrate [revision]` | Apply migrations: latest by default; `-1` = one step back, `base` = remove all, or a revision id. Prints the current revision afterwards |
| `revision "message"` | Autogenerate a migration from model changes. **Always review the generated file** |
| `reset` | **Destructive:** removes containers and all Cortex volumes. Asks you to type `delete cortex data`; anything else (or no input) aborts |

Exit codes: 0 on success, 1 when any step fails (failures of `docker`, `uv` and `pytest` propagate), 2 for an unknown command.

**Why `dev.cmd` and not just `dev.ps1`?** Windows' default execution policy (Restricted) blocks local `.ps1` files. `dev.cmd` starts PowerShell with `-ExecutionPolicy Bypass` for that one process only; no system setting changes. If you prefer running `.\scripts\dev.ps1` directly, allow local scripts for your user. That is your security decision:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Why the script parses its own arguments:** with a PowerShell `param()` block, pytest flags would be captured by PowerShell. `-m` would be an unknown parameter, `-p` would bind to `-Port`, and `-v` would become `-Verbose`. The script takes only `-App`, `-Follow` and `-Port` for the commands that use them; `test`/`check` forward everything to pytest.

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

## Continuous integration (TASK-01.09)

`.github/workflows/ci.yml` runs on every pull request, on pushes to `main`, and manually (`workflow_dispatch`).

| Job | What it proves |
|---|---|
| **Backend** | `uv sync --frozen` (lockfile must be current) → ruff → format check → strict mypy → **full** pytest, including integration tests against real PostgreSQL 17.12, Redis 7.4.12 and Qdrant 1.20.2 service containers on the same host ports as local compose |
| **Docker image** | Builds the production image (buildx, GitHub Actions cache), runs it read-only with all capabilities dropped, and checks it becomes healthy, runs as uid 10001, contains no dev dependencies, and logs only JSON |

The local equivalent of the backend job is `scripts\dev.cmd check` (with the compose stack up).

**Supply-chain hardening:** third-party actions are pinned to full commit SHAs (the version is in a comment), the token is read-only (`permissions: contents: read`), checkout does not persist credentials, and a newer push cancels the outdated run.

**Validate a workflow change locally** before pushing:
```powershell
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:1.7.12
```

**Updating a pinned action:** look up the new release's commit, then replace both the SHA and the comment:
```powershell
gh api repos/actions/checkout/releases/latest --jq .tag_name
gh api repos/actions/checkout/commits/<tag> --jq .sha
```

## Database layer (TASK-02.01)

| Piece | Where | Notes |
|---|---|---|
| `Base` | `app/database/base.py` | Declarative base; constraint naming convention; `uuid.UUID` → native `uuid`, `datetime` → `timestamptz`; `eager_defaults=True` |
| `UUIDPrimaryKeyMixin` | same | `id` = app-generated **UUIDv7** (`app/utils/ids.py`): time-ordered, so inserts append to the PK index instead of scattering |
| `TimestampMixin` | same | `created_at` / `updated_at` set by PostgreSQL `now()` (transaction start time); `updated_at` refreshed on UPDATE |
| `create_engine(settings)` | `app/database/session.py` | One pool per process: `pool_pre_ping`, 30-min recycle, `application_name` visible in `pg_stat_activity` |
| `DbSession` dependency | `app/dependencies/database.py` | One `AsyncSession` per request; always closed; rolled back on error |

```python
from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    name: Mapped[str] = mapped_column(String(200))
```

**Rules**
- Services commit; repositories never do. Whatever is not committed when the request ends is rolled back.
- Never trigger lazy loading in async code: load relationships explicitly (`selectinload`), or read only columns already loaded. `expire_on_commit=False` and `eager_defaults=True` keep objects readable after commit and update.
- Test-only models go on their own `MetaData` (see `tests/integration/test_database_session.py`), so they never reach `Base.metadata`, which Alembic reads.
- The engine is created in `create_app` (no I/O) and disposed in the lifespan shutdown.

Naming convention results: `pk_<table>`, `uq_<table>_<cols>`, `ix_<table>_<cols>`, `fk_<table>_<cols>_<referred_table>`, `ck_<table>_<name>`.

## Migrations (TASK-02.02)

Alembic with the async engine. Configuration lives in `backend/alembic.ini` and `backend/migrations/env.py`.

**Workflow for a schema change**
1. Change or add a model in `app/models/`. A new module must also be imported in `app/models/__init__.py` (the model registry), or Alembic never sees it.
2. `scripts\dev.cmd revision "add documents table"`: autogenerates `backend/migrations/versions/<date>_<time>-<rev>_<slug>.py`, formatted by ruff.
3. **Review it by hand.** Autogenerate misses some things: renames (it sees drop + add), data migrations, `CHECK` constraint bodies, and extension or enum changes.
4. `scripts\dev.cmd migrate`, then `scripts\dev.cmd check`.

**Design choices**
- **No URL in `alembic.ini`.** `env.py` uses the same `CORTEX_DATABASE__*` settings as the app. Tests and tools can override it via `config.attributes["database_url"]` (attributes, because configparser would interpret `%` in passwords).
- Autogenerate compares **types and server defaults** too (`compare_type`, `compare_server_default`).
- Constraint names come from the `Base` naming convention (`pk_…`, `uq_…`, `fk_…`, `ix_…`, `ck_…`), so later migrations can drop or alter them by name.
- File names start with a UTC date and time, so `ls` shows them in order.
- Logging uses the app's JSON pipeline; set `CORTEX_LOG_FORMAT=console` for readable CLI output.
- On Windows, `env.py` runs on the selector event loop (psycopg, ADR-018).
- Migrations ship in the Docker image: `docker run <image> alembic upgrade head` runs the exact schema version that matches the code (verified read-only, no capabilities).

**Safety net (`tests/integration/test_migrations.py`, each test on its own scratch database)**

| Test | Catches |
|---|---|
| round trip (head → base → head) | a downgrade that leaves tables behind; a migration that cannot be re-applied |
| stairway (each revision up, down, up) | a broken `downgrade()` in any single revision |
| models ↔ migrations in sync | a model change without a migration, or the reverse |
| single head | two branches that both added a migration (needs `alembic merge`) |
| no URL in `alembic.ini` | credentials creeping into config files |
| offline SQL | `alembic upgrade head --sql` for reviewing SQL without a database |

These tests were proven to have teeth with a temporary probe model (see the TASK-02.02 session log): the drift test failed without a migration and passed after `revision`, and the round-trip and stairway tests failed when `downgrade()` was broken.

## Errors (TASK-02.06)

Services raise **domain errors** from `app/core/errors.py`; they never raise `HTTPException` and never import FastAPI. The API layer (`app/api/errors.py`) maps them to status codes in one table, `STATUS_BY_ERROR`.

```python
from app.core.errors import ConflictError, NotFoundError

raise NotFoundError("Knowledge base not found", code="KNOWLEDGE_BASE_NOT_FOUND")
raise ConflictError(
    "This file already exists in the knowledge base.",
    code="DOCUMENT_DUPLICATE",
    details={"existing_document_id": str(existing.id)},
)
```

| Error | Status | Default code |
|---|---|---|
| `BadRequestError` | 400 (malformed requests, e.g. `INVALID_CURSOR`) | `BAD_REQUEST` |
| `AuthenticationError` | 401 (+ `WWW-Authenticate: Bearer`) | `UNAUTHENTICATED` |
| `PermissionDeniedError` | 403 | `PERMISSION_DENIED` |
| `NotFoundError` | 404 (also for other tenants' resources: never confirm existence) | `NOT_FOUND` |
| `ConflictError` | 409 | `CONFLICT` |
| `PayloadTooLargeError` | 413 | `PAYLOAD_TOO_LARGE` |
| `UnsupportedMediaTypeError` | 415 | `UNSUPPORTED_MEDIA_TYPE` |
| `ValidationFailedError` | 422 (business rules; schema errors come from FastAPI) | `VALIDATION_ERROR` |
| `RateLimitedError(retry_after_seconds=…)` | 429 (+ `Retry-After`) | `RATE_LIMITED` |
| `UpstreamUnavailableError` | 503 | `UPSTREAM_UNAVAILABLE` |
| plain `AppError` | 400 | `ERROR` |
| anything unexpected | 500 | `INTERNAL_ERROR` (fixed generic message) |

Every error body: `{"error": {"code", "message", "details", "request_id"}}`; `request_id` equals the `X-Request-ID` header.

**Rules**
- `message` and `details` are sent to the client: no secrets, SQL, stack traces or other tenants' data.
- Codes are a public contract: pick specific ones (`DOCUMENT_DUPLICATE`) and never rename them.
- A new `AppError` subclass must be added to `STATUS_BY_ERROR` (a test fails otherwise).
- 422 field errors contain only `loc`, `msg` and `type`. FastAPI's default would echo the submitted value, e.g. a password.
- Unexpected exceptions are logged **once** by `RequestContextMiddleware`, with the request id and a redacted, structured traceback. Verified on a real uvicorn: 1 log record, 0 uvicorn duplicates, 0 plain-text tracebacks.

## Pagination (TASK-02.07)

Every list endpoint uses **keyset pagination**: `?limit=1..100` (default 20) and `?cursor=<next_cursor from the previous page>`. Responses are `{"items": [...], "next_cursor": "..." | null, "has_more": bool}` (`app/schemas/pagination.py`).

```python
from app.dependencies.pagination import CursorCodecDep, PageParams
from app.repositories.pagination import KeysetOrder, fetch_page

NEWEST_FIRST = KeysetOrder(Document.created_at, Document.id, descending=True)

# in a repository/service
stmt = select(Document).where(Document.organization_id == org_id, Document.knowledge_base_id == kb_id)
result = await fetch_page(session, stmt, NEWEST_FIRST, page=page, codec=codec,
                          scope=f"documents:kb={kb_id}:created_at:desc")
```

**Why keyset, not OFFSET:** the next page is `WHERE (created_at, id) < (:last_created_at, :last_id)`. It costs the same on page 1 and page 1,000, and rows inserted or deleted between requests cause no duplicates and no skips (both tested). With 20,000 rows, `EXPLAIN ANALYZE` reads 22 index rows for page 1 and for a page ~9,000 rows deep.

**Rules**
- The sort column must be NOT NULL, and the `id` tie-breaker is mandatory: rows written in one transaction share `now()`. A test pages 45 rows with identical timestamps.
- The `scope` names the listing *and its filters and order*. A cursor from another listing (another KB, another order) is rejected.
- Cursors are **signed, not encrypted** (HMAC-SHA256 with a key derived from the JWT secret): readable but not forgeable. Never use a secret value as a sort key. Rotating the JWT secret invalidates outstanding cursors; clients restart from page one.
- Tenant isolation comes from the query's `WHERE organization_id = …`, never from the cursor.
- Every cursor problem (tampered, too long, wrong listing, garbage) → **400 `INVALID_CURSOR`**; `limit` out of range → 422.
- Known follow-up (TASK-14.06): indexes are `(…, created_at DESC)` without `id`, so Postgres adds a small incremental sort to break timestamp ties. Add `id DESC` to the index if measurements show large tie groups.

## Passwords (TASK-03.01, ADR-021)

`app/core/passwords.py`:

```python
from app.core.passwords import PasswordHasher, validate_password

hasher = PasswordHasher()
validate_password(raw, email=email)            # raises ValidationFailedError(422, PASSWORD_POLICY)
stored = await hasher.hash_async(raw)          # "$argon2id$v=19$m=19456,t=2,p=1$..."
result = await hasher.verify_async(raw, stored)
if result.valid and result.new_hash:           # parameters changed since the hash was made
    user.password_hash = result.new_hash       # transparent upgrade on login
await hasher.dummy_verify_async(raw)           # unknown email: same timing as a real check
```

- Argon2id m=19 MiB, t=2, p=1: ~53 ms per hash or verify on the dev machine.
- **Always use the `*_async` methods from async code.** Hashing is CPU-bound and would otherwise stall the event loop for every other request (a test proves the async path keeps the loop responsive).
- Policy: 12 to 128 characters, not only whitespace, not the email or its local part. No "must contain a symbol" rules (NIST SP 800-63B). Input is NFKC-normalised, so equivalent Unicode forms match.
- A corrupt or foreign hash in the database verifies as `False`; it never crashes a login.

## Access tokens (TASK-03.02, ADR-006)

`app/core/tokens.py`, available as `app.state.access_tokens`:

```python
issued = tokens.issue(user.id)             # AccessToken(token, expires_at, expires_in)
claims = tokens.verify(bearer_token)       # AccessTokenClaims(user_id, token_id, issued_at, expires_at)
```

| Property | Value |
|---|---|
| Algorithm | HS256, **pinned on verify** (`alg: none` and other algorithms rejected, even if correctly signed) |
| Lifetime | `CORTEX_AUTH__ACCESS_TOKEN_TTL_SECONDS` (default 900 s), 30 s clock-skew leeway |
| Required claims | `sub` (user id), `iat`, `exp`, `jti`, `typ="access"`, `iss="cortex"`, `aud="cortex-api"` |
| Signing key | Derived from the JWT secret for this purpose only (the raw secret cannot sign valid tokens; tested) |
| Errors | `TOKEN_EXPIRED` → refresh; `INVALID_TOKEN` → log in again. Both 401 with `WWW-Authenticate: Bearer` |

- Tokens carry **only the user id**. JWTs are signed, not encrypted: anyone holding one can read it.
- Roles are **not** in the token. Permissions are checked per request against `organization_memberships`, so a role change or removal applies immediately rather than after up to 15 minutes.
- Access tokens are not revocable individually (ADR-006). Logout revokes the refresh-token family; an already-issued access token stays valid until it expires (≤ 15 min).

## Refresh tokens and sessions (TASK-03.03, ADR-006, ADR-022)

| Piece | Where |
|---|---|
| Token generation (256-bit, URL-safe) and SHA-256 hashing | `app/core/refresh_tokens.py` |
| Cookie set/clear (`cortex_refresh`; HttpOnly, SameSite=Strict, Path=/api/v1/auth, Secure per settings) | `app/api/auth_cookies.py` |
| Row locking, family revocation | `app/repositories/refresh_tokens.py` |
| `SessionService.start / rotate / end / end_all_for_user` | `app/services/sessions.py` |

**Lifecycle**
1. Login → `start(user_id)` creates a new **family** with one token. Only its SHA-256 is stored.
2. Refresh → `rotate(token)` locks the row (`FOR UPDATE`); the token is marked replaced and a new one is issued in the same family (sliding `refresh_token_ttl_days`).
3. **Reuse** (a replaced or revoked token comes back) → the whole family is revoked, **committed before** the 401, and logged as `auth.refresh_token_reuse_detected`. The response is identical to "unknown token".
4. Logout → `end(token)` revokes the family (idempotent). Password change → `end_all_for_user(user_id, keep_family=current)`.
5. Inactive users cannot refresh; their session family is revoked.

**Frontend contract:** never send two refreshes with the same token concurrently (single-flight). The second would count as reuse and sign the user out (ADR-022, by design).

**Verified by mutation tests:** disabling reuse detection fails 5 tests; dropping the commit before the reuse error (the revocation would roll back silently) fails 2.

## Auth endpoints (TASK-03.04)

Contract: [API_SPECIFICATION.md](../../API_SPECIFICATION.md) (Authentication). Code: `app/api/v1/auth.py` (routes), `app/services/auth.py` (rules), `app/dependencies/auth.py` (`CurrentUser`, `AuthServiceDep`).

```python
from app.dependencies.auth import CurrentUser

@router.get("/something")
async def something(user: CurrentUser) -> ...:   # 401 unless a valid Bearer token for an active user
```

**Try it in Swagger UI** (`scripts\dev.cmd api`, then http://127.0.0.1:8000/api/v1/docs): call `POST /auth/register`, copy `access_token`, click **Authorize**, paste it, then call `GET /auth/me`. The browser keeps the refresh cookie automatically. `POST /auth/refresh` needs the header `X-Requested-With: cortex`.

Security behaviour (all tested, two by mutation):
- Login: unknown email, wrong password and deactivated account give the same 401, in the same time (measured without the dummy verify: 12.8 ms vs 56.8 ms, an account-discovery leak).
- `CurrentUser` re-checks the database on every request: a deactivated user is locked out immediately, not when the token expires.
- Registration is atomic and race-safe (3 concurrent identical sign-ups → one 201, two 409, nothing left behind).
- The refresh cookie only travels to `/api/v1/auth/*` (verified with curl's cookie jar against a real server).

## Organization context and roles (TASK-03.05, ADR-005)

Every route under `/api/v1/orgs/{org_id}/…` takes one of these dependencies (`app/dependencies/orgs.py`):

```python
from app.dependencies.orgs import AdminContext, OrgContextDep, OwnerContext

@router.get("/orgs/{org_id}/knowledge-bases")
async def list_kbs(ctx: OrgContextDep, ...):        # any member
    ...  # query with ctx.organization_id, never the raw path value

@router.post("/orgs/{org_id}/knowledge-bases")
async def create_kb(ctx: AdminContext, ...):        # ADMIN or OWNER
```

| Situation | Response |
|---|---|
| No/invalid token | 401 (checked first, so anonymous callers learn nothing about organizations) |
| Not a member, or the organization does not exist | **404 `ORGANIZATION_NOT_FOUND`**, identical body in both cases |
| Member, but role below the route's minimum | 403 `PERMISSION_DENIED`, `details.required_role` |
| Malformed `org_id` | 422 |

- The role is read from `organization_memberships` on **every** request. Promotions, demotions and removals apply to the very next request; roles are never in the token.
- The path parameter **must** be named `org_id`.
- Log lines of the request (including the access log) carry `user_id` and `organization_id`. The middleware removes them when the request ends.

**Lesson from the mutation test:** a membership lookup that forgot the `organization_id` filter passed every role-matrix test (they only use the caller's own organization) and failed only the cross-tenant tests. Every new org-scoped route therefore gets an explicit "member of another org gets 404" test (enforced from TASK-03.08).

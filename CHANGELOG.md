# Changelog

All notable changes to Cortex are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer once code exists.

## [Unreleased]

### Added
- 2026-11-26 — Password hashing with Argon2id (OWASP parameters, transparent rehash on login, NFKC normalisation, constant-time dummy verify, off-event-loop async API) and a NIST-style password policy [TASK-03.01].
- 2026-11-26 — Test harness: random test order on every run (pytest-randomly), integration tests auto-marked by folder, persisted factories, an `api_client` on the migrated scratch database, faster isolation (integration suite ~43 s → ~28 s), and docs/testing/README.md [TASK-02.08].
- 2026-11-26 — Keyset pagination for list endpoints: `{items, next_cursor, has_more}`, signed cursors bound to one listing, stable under concurrent inserts/deletes, 400 `INVALID_CURSOR` for any bad cursor [TASK-02.07].
- 2026-11-26 — Consistent error responses: domain exceptions mapped centrally to `{"error": {code, message, details, request_id}}` for every non-2xx (including 404/405/422/500); 422 no longer echoes submitted values; unexpected errors return a generic 500 and are logged once with the request id [TASK-02.06].
- 2026-11-26 — Chat and usage schema: conversations (KB tenant FK; ownership requires organization membership), messages (role/status CHECKs, token/latency metrics, retrieval trace), citations (snapshot that survives document deletion), usage events (exact numeric(12,6) costs that survive deletions) [TASK-02.05].
- 2026-11-26 — Document schema: knowledge bases (soft delete, per-org case-insensitive names, chunking CHECKs), documents (per-KB de-duplication via partial unique index), chunks (deterministic ids, range CHECKs), processing jobs (one active job per document). Composite foreign keys make cross-tenant and cross-KB rows impossible at the database level [TASK-02.04].
- 2026-11-26 — Identity schema: users (case-insensitive unique email), organizations, organization memberships (OWNER/ADMIN/MEMBER with a database CHECK), refresh tokens (hashed, rotation family, self-reference); first migration; relationships require explicit loading [TASK-02.03].
- 2026-11-26 — Alembic migrations: async env with the settings-driven URL, naming-convention constraint names, ruff post-write hook, `dev migrate` / `dev revision`, migrations shipped in the image, and migration safety tests (round trip, stairway, model drift, single head) [TASK-02.02].
- 2026-11-26 — Database foundation: async SQLAlchemy engine + session-per-request dependency, declarative Base with constraint naming convention, UUIDv7 primary-key and server-timestamp mixins, pool disposal on shutdown [TASK-02.01].
- 2026-11-26 — GitHub Actions CI: lint, format, strict mypy, full tests with PostgreSQL/Redis/Qdrant service containers, Docker image build + hardened smoke test; actions pinned to commit SHAs [TASK-01.09].
- 2026-11-26 — `scripts/dev.cmd` / `scripts/dev.ps1` developer commands: up (-App), down, ps, logs, api, test (pytest args pass through), lint, format, check, reset (typed confirmation) [TASK-01.08].
- 2026-11-26 — Structured JSON logging for every logger (structlog + stdlib), `X-Request-ID` propagation, access log without query strings, recursive secret redaction, `CORTEX_LOG_FORMAT` [TASK-01.07].
- 2026-11-26 — Backend container image: two-stage uv build, non-root, read-only root FS, all capabilities dropped, liveness health check; `api` compose service behind the opt-in `app` profile on 127.0.0.1:18000 [TASK-01.05].
- 2026-11-26 — FastAPI app factory with `/api/v1/health/live` and `/api/v1/health/ready` (concurrent, time-bounded checks of PostgreSQL, Redis, Qdrant; 503 when any is down) [TASK-01.06].
- 2026-11-26 — Local infrastructure: Docker Compose with PostgreSQL 17.12, Redis 7.4.12, Qdrant 1.20.2 (non-root), health checks, named volumes, localhost-only ports 15432/16379/16333/16334 [TASK-01.04].
- 2026-11-26 — Typed settings (`app.config`): `CORTEX_*` env vars with nested groups, `.env.example`, staging/production refuse unsafe defaults, unknown variables rejected, secrets masked [TASK-01.03].
- 2026-11-26 — Backend scaffold: uv project pinned to Python 3.12, `app` package tree per ARCHITECTURE.md, ruff + strict mypy + pytest configuration, project-layout tests [TASK-01.02].
- 2026-11-26 — Repository hygiene: `.gitignore`, `.gitattributes`, `.editorconfig` [TASK-01.01].
- 2026-11-26 — Project planning documents: PROJECT_PLAN, TASK_TRACKER, ARCHITECTURE, DATABASE_DESIGN, API_SPECIFICATION, TECH_STACK, DEVELOPMENT_GUIDELINES, DECISIONS, CHANGELOG, SESSION_LOG, README, and the `docs/` structure.

### Changed
- 2026-11-26 — Local defaults use `127.0.0.1` instead of `localhost` (Windows IPv6 fallback cost ~2–10 s per connection) [TASK-01.06].

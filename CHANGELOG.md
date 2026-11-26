# Changelog

All notable changes to Cortex are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer once code exists.

## [Unreleased]

### Added
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

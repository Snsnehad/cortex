# Changelog

All notable changes to Cortex are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer once code exists.

## [Unreleased]

### Added
- 2026-11-26 — FastAPI app factory with `/api/v1/health/live` and `/api/v1/health/ready` (concurrent, time-bounded checks of PostgreSQL, Redis, Qdrant; 503 when any is down) [TASK-01.06].
- 2026-11-26 — Local infrastructure: Docker Compose with PostgreSQL 17.12, Redis 7.4.12, Qdrant 1.20.2 (non-root), health checks, named volumes, localhost-only ports 15432/16379/16333/16334 [TASK-01.04].
- 2026-11-26 — Typed settings (`app.config`): `CORTEX_*` env vars with nested groups, `.env.example`, staging/production refuse unsafe defaults, unknown variables rejected, secrets masked [TASK-01.03].
- 2026-11-26 — Backend scaffold: uv project pinned to Python 3.12, `app` package tree per ARCHITECTURE.md, ruff + strict mypy + pytest configuration, project-layout tests [TASK-01.02].
- 2026-11-26 — Repository hygiene: `.gitignore`, `.gitattributes`, `.editorconfig` [TASK-01.01].
- 2026-11-26 — Project planning documents: PROJECT_PLAN, TASK_TRACKER, ARCHITECTURE, DATABASE_DESIGN, API_SPECIFICATION, TECH_STACK, DEVELOPMENT_GUIDELINES, DECISIONS, CHANGELOG, SESSION_LOG, README, and the `docs/` structure.

### Changed
- 2026-11-26 — Local defaults use `127.0.0.1` instead of `localhost` (Windows IPv6 fallback cost ~2–10 s per connection) [TASK-01.06].

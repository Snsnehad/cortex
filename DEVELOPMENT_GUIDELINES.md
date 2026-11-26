# Cortex — Development Guidelines

## 1. Working agreement
1. Small, independently verifiable tasks — one task ID at a time, from TASK_TRACKER.md.
2. Inspect files before modifying them. Never delete existing work without explicit approval.
3. A task is `COMPLETED` only with passing, **executed** tests, satisfied acceptance criteria and updated docs. Test output is recorded in the tracker.
4. Explain before implementing: what, why, concepts, expected files. Then implement, test, show results, update tracker, name the next task.
5. Ask before major architectural changes; record accepted changes as ADRs in DECISIONS.md.
6. Destructive commands (dropping DBs/volumes, `git reset --hard`, deleting files, any production action) are explained and approved first.
7. No paid cloud resources without an approved cost estimate.

## 2. Session protocol
* **Start** ("Resume Cortex development"): read PROJECT_PLAN, TASK_TRACKER, SESSION_LOG, DECISIONS → inspect repo + `git status` → run relevant tests → reconcile tracker with reality → short summary → continue the exact next task.
* **End**: update TASK_TRACKER and SESSION_LOG (date, phase, tasks, files, decisions, commands, tests + results, blockers, known bugs, exact next task, resume instructions), CHANGELOG if user-visible, commit.

## 3. Python code
* PEP 8 via `ruff format` + `ruff check`; type hints everywhere; `mypy --strict` on `app/`.
* Layering (see ARCHITECTURE §4): routes → services → repositories. No SQL outside repositories; no `HTTPException` outside the API layer.
* Every repository method touching tenant data takes `organization_id` explicitly.
* Dependency injection via FastAPI `Depends` and provider factories; no module-level singletons holding connections.
* Exceptions: never `except Exception: pass`. Catch specific errors, add context, re-raise as domain errors, log once at the boundary.
* Use `Decimal` for money, timezone-aware `datetime` (UTC) only, `pathlib` for paths.
* No placeholder implementations in code marked complete; unfinished code paths raise `NotImplementedError` and the task stays open.

## 4. Configuration & secrets
* All configuration from environment variables (`CORTEX_*`); `.env` only for local dev and never committed; `.env.example` lists every variable with safe defaults/comments.
* Never print secrets in logs, terminal output, docs or test snapshots.

## 5. Database
* Every schema change is an Alembic migration, reviewed by hand, with a working downgrade.
* Services own transactions; repositories never commit. Long network calls never run inside an open transaction.

## 6. Testing
* Layout: `tests/unit`, `tests/integration` (real PG/Redis/Qdrant in Docker), `tests/tenant_isolation`, `tests/workers`, `tests/rag`, `tests/eval` (opt-in, slow).
* Tests must not need internet or API keys: fake embedder/LLM providers, respx for HTTP, moto for S3.
* Every new org-scoped route gets an isolation case (enforced by the route registry test).
* Markers: `@pytest.mark.slow`, `@pytest.mark.eval`, `@pytest.mark.llm` (real LLM, manual only).

## 7. Git
* Branches: `main` (always green) + short-lived `feat/TASK-xx.yy-short-name`.
* Commits small and meaningful, Conventional Commits style with task id: `feat(auth): rotate refresh tokens [TASK-03.03]`.
* Never commit `.env`, uploaded files, model caches, or generated eval outputs with document text.

## 8. API
* Follow API_SPECIFICATION.md conventions (error envelope, keyset pagination, status codes). Any new endpoint is documented there in the same commit.

## 9. Frontend
* API calls only in `src/api/*` service modules; components consume services/stores.
* Every data view handles loading, empty and error states.
* Access token in memory only; never in localStorage.

## 10. Logging
* Structured JSON with ids only. Never log passwords, tokens, API keys, Authorization headers or document text (lengths/hashes are fine).

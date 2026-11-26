# Cortex — Session Log

Newest session first. Each entry is the handoff for the next session.

---

## Session 1 — 2026-11-26

**Phase:** PHASE-00 (Planning & architecture) — complete pending your approval.

**Tasks completed / moved**
- TASK-00.01 COMPLETED — environment inspected.
- TASK-00.02 … 00.06 NEEDS_REVIEW — planning documents written.
- TASK-00.07 BLOCKED — waiting for plan approval.

**Environment findings**
- `D:` accessible, ~118 GB free. `D:\Projects\Cortex` did not exist → created.
- Python: no system `python` on PATH (Windows Store alias only). `uv 0.13.6` installed with CPython 3.12.16 and 3.13.18 → use uv.
- Docker Desktop 29.9.2, Compose v5.6.1, daemon running, 12 CPUs, **~2.8 GB RAM allocated** (recommend 6–8 GB).
- Git 2.56 installed. Repo **not** initialised yet (TASK-01.01).
- **Node.js/npm not installed** (needed in Phase 12).

**Files created**
- Root: PROJECT_PLAN.md, TASK_TRACKER.md, ARCHITECTURE.md, DATABASE_DESIGN.md, API_SPECIFICATION.md, TECH_STACK.md, DEVELOPMENT_GUIDELINES.md, DECISIONS.md, CHANGELOG.md, SESSION_LOG.md, README.md
- `docs/{architecture,database,api,development,deployment,testing,decisions}/README.md`

**Decisions made (Proposed, see DECISIONS.md):** ADR-001 … ADR-015. Open questions P-1 … P-5.

**Commands executed:** read-only environment checks (`Get-PSDrive`, `Get-Command`, `uv python list`, `docker info`, version checks); `New-Item -ItemType Directory` for the project and docs folders.

**Tests executed:** none (no code yet).

**Blockers:** plan approval; B-01 Node missing; B-02 Docker memory; B-03 LLM provider/key; B-04 Git remote.

**Known bugs:** none.

**Update (same session, after approval):**
- Plan approved in chat → PHASE-00 COMPLETED; ADR-001…015 Accepted. P-4 answered: repo goes to your **personal** GitHub. P-1, P-2, P-3, P-5 unanswered → recommended defaults apply.
- TASK-01.01 IN_PROGRESS: `git init -b main`; created `.gitignore`, `.gitattributes`, `.editorconfig`.
- Fixed in my own draft: an unanchored `storage/` ignore rule would have hidden the source package `backend/app/storage/`; runtime-data folders are now anchored to the repo root.
- Test: `git check-ignore --no-index -v` matrix → 18/18 secret/cache/runtime paths ignored, 8/8 source/docs paths kept (incl. `.env.example`, `backend/app/storage/*.py`). PASS.
- Found: global git identity and `gh` login are both the **work** account (`snehad-bot`, SSH). The initial commit is held until a repo-local personal identity is set (B-04).

- Repo-local identity set (Snsnehad, personal Gmail) and initial commit made → **TASK-01.01 COMPLETED**. Global work identity untouched.

- Created public repo https://github.com/Snsnehad/cortex and pushed `main` (B-04 resolved). Note: `gh` active account is now Snsnehad — for yenta work run `gh auth switch -u snehad-bot`.

- **TASK-01.02 COMPLETED** on branch `feat/TASK-01.02-backend-scaffold` (merged to `main`): `backend/pyproject.toml` (ruff, strict mypy, pytest config), `uv.lock`, 23 documented `app` packages, test layout. Commands: `uv add --dev ruff mypy pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`. Results: all pass; pytest 25 passed; a deliberate stray folder without `__init__.py` made the layout test fail as intended (then removed).
- Decision: no runtime dependencies yet; each is added by the task that first needs it. `uv link-mode=copy` because cache (C:) and project (D:) are on different drives.

- **TASK-01.03 COMPLETED** (branch `feat/TASK-01.03-settings`, merged to `main`): `app/config/settings.py` with groups database, redis, qdrant, storage, auth, embeddings, llm, rag; `.env.example` at repo root. Added deps pydantic 2.14.4, pydantic-settings 2.16.3 + pydantic mypy plugin. Results: ruff/format/mypy pass, pytest 53 passed (28 new); negative checks all failed as intended (see tracker). New ADR-016.

- **TASK-01.04 COMPLETED** (branch `feat/TASK-01.04-compose-infra`, merged to `main`). Docker Desktop was not running at session time → started it. First `up` failed: host port 6379 held by the **yenta-redis** work container (also `chatbot-qdrant-1` maps 6333) → Cortex moved to host ports 15432/16379/16333/16334; settings defaults, `.env.example` and a test updated to match (ADR-017). Verified: all healthy, config in effect, Qdrant non-root, health-check negatives, localhost-only (not on LAN), volume persistence across down/up. Work containers untouched. Cortex stack left **running**.

- Order changed with approval: TASK-01.06 before 01.05.
- **TASK-01.06 COMPLETED** (branch `feat/TASK-01.06-app-factory-health`, merged to `main`): app factory, `/api/v1/health/live` + `/ready`, HealthService with concurrent 2 s-bounded checks, settings on `app.state`. Deps added: fastapi 0.165.0, uvicorn 0.54.0, psycopg[binary] 3.3.9, redis 8.2.0, httpx 0.28.1. Tests: 68 passed (15 new; 2 integration vs live stack). Manual: real uvicorn, stop/start redis and postgres → 503/200 transitions as expected.
- Found + fixed (ADR-018): `localhost` costs 2–10 s per async connection on Windows → defaults now `127.0.0.1`; psycopg async fails on the Proactor loop → selector loop in tests and `--loop asyncio:SelectorEventLoop` for non-reload uvicorn on Windows.
- Mistake caught: first manual run left a stray uvicorn running (cleanup matched `python.exe` by a pattern that missed `uvicorn.exe"`), so a later run hit the old server. Now stopped by port owner; documented.

- **TASK-01.05 COMPLETED** (branch `feat/TASK-01.05-api-dockerfile`, merged): `backend/Dockerfile` (python:3.12.16-slim-trixie, uv 0.13.6, two stages), `.dockerignore`, compose `api` service (profile `app`, 127.0.0.1:18000, read-only, cap_drop ALL, no-new-privileges). All verification passed (see tracker). Found + fixed: uvloop log spam during outages → `--loop asyncio` (ADR-019), proven with a side-by-side probe container (removed). API container stopped after verification; infra left running.

- **TASK-01.07 COMPLETED** (branch `feat/TASK-01.07-structured-logging`, merged): structlog 26.1.0; `app/core/logging.py` (one pipeline, redaction), `app/middleware/request_context.py` (pure ASGI), new setting `log_format`. Tests 119 passed (51 new). Verified in the container (see tracker). Issues found + fixed: stale-stdout handler (pytest closes setup-phase capture streams) → per-record `sys.stdout`; empty-user URL credentials; uvicorn `color_message` noise.
- Known gap: an unhandled exception is logged by our middleware (with request_id) and also by uvicorn (without) until the error handling in TASK-02.06.

- **TASK-01.08 COMPLETED** (branch `feat/TASK-01.08-dev-scripts`, merged): `scripts/dev.ps1` (PS 5.1, pure ASCII, CRLF) + `scripts/dev.cmd` (per-process ExecutionPolicy Bypass: the machine policy is Restricted by default). Fixed during task: PowerShell param binding would have captured pytest flags (-m, -p→-Port, -v→-Verbose) → manual argument parsing. Every command run and verified (see tracker). Real `reset` not run (destructive).

- TASK-01.09 IN PROGRESS: `.github/workflows/ci.yml` written (actions SHA-pinned, read-only token), actionlint clean, pushed on branch `feat/TASK-01.09-ci`, PR #1 opened: https://github.com/Snsnehad/cortex/pull/1. Waiting for the first run.

**Exact next task:** finish **TASK-01.09**: check PR #1 CI (`gh pr checks 1 --repo Snsnehad/cortex`). If red, fix on the branch. If green, merge (fast-forward main), confirm the run on main, mark TASK-01.09 + PHASE-01 COMPLETED. Then **TASK-02.01** (SQLAlchemy engine, session, Base).

**How to resume:** say "Resume Cortex development". The assistant reads PROJECT_PLAN, TASK_TRACKER, this log and DECISIONS, checks the repo state, then continues from the next task.

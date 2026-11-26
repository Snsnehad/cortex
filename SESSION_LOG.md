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

**Exact next task:** **TASK-01.02** (backend scaffold: uv project, Python 3.12, `app` package tree, ruff/mypy/pytest config).

**How to resume:** say "Resume Cortex development". The assistant reads PROJECT_PLAN, TASK_TRACKER, this log and DECISIONS, checks the repo state, then continues from the next task.

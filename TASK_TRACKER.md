# Cortex — Task Tracker

> Source of truth for **status**. Definitions and completion conditions live in [PROJECT_PLAN.md](PROJECT_PLAN.md).
> A task is `COMPLETED` only when: implementation finished, relevant tests **executed and passing**, acceptance criteria met, docs updated.
> Statuses: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED` · `NEEDS_REVIEW`
> Last updated: 2026-11-26 (Session 1, after plan approval)

## Summary

| Phase | Name | Status | Done / Total |
|---|---|---|---|
| PHASE-00 | Planning & architecture | COMPLETED | 7 / 7 |
| PHASE-01 | Dev environment & repo | IN_PROGRESS | 4 / 9 |
| PHASE-02 | Database & backend foundation | NOT_STARTED | 0 / 8 |
| PHASE-03 | Auth & multi-tenancy | NOT_STARTED | 0 / 9 |
| PHASE-04 | Upload & storage | NOT_STARTED | 0 / 6 |
| PHASE-05 | Background processing | NOT_STARTED | 0 / 6 |
| PHASE-06 | Extraction & chunking | NOT_STARTED | 0 / 7 |
| PHASE-07 | Embeddings & Qdrant | NOT_STARTED | 0 / 7 (+1 stretch) |
| PHASE-08 | Dense & sparse retrieval | NOT_STARTED | 0 / 5 |
| PHASE-09 | Hybrid & reranking | NOT_STARTED | 0 / 4 |
| PHASE-10 | RAG & LLM | NOT_STARTED | 0 / 7 |
| PHASE-11 | Chat & conversations | NOT_STARTED | 0 / 4 (+1 stretch) |
| PHASE-12 | Frontend | NOT_STARTED | 0 / 12 |
| PHASE-13 | Analytics & monitoring | NOT_STARTED | 0 / 3 (+1 stretch) |
| PHASE-14 | Testing & performance | NOT_STARTED | 0 / 7 |
| PHASE-15 | Security & prod readiness | NOT_STARTED | 0 / 5 |
| PHASE-16 | Production deployment | NOT_STARTED | 0 / 5 |
| PHASE-17 | Documentation & portfolio | NOT_STARTED | 0 / 4 |

**Current task:** TASK-01.05 — backend Dockerfile (multi-stage, uv, non-root) + `api` service in compose.
**Next task:** TASK-01.06 (app factory + /health/live and /health/ready).

## Known blockers

| ID | Blocker | Blocks | Resolution |
|---|---|---|---|
| B-01 | Node.js/npm not installed | TASK-12.01+ | Install Node 22 LTS before Phase 12 |
| B-02 | Docker Desktop has ~2.8 GB RAM | Comfortable running of Phase 5+ stack | Raise to 6–8 GB (Settings → Resources) |
| B-03 | No LLM endpoint/API key confirmed | Manual RAG runs (TASK-10.07), faithfulness eval (TASK-14.04) | Decide provider (see DECISIONS.md "Pending your input") |
| ~~B-04~~ | ~~Personal GitHub remote~~ | — | **Resolved 2026-11-26**: public repo https://github.com/Snsnehad/cortex, `origin` over HTTPS, gh active account = Snsnehad (has `workflow` scope for CI) |

---

## Task detail

Column key: **Dep** = dependencies · **Files** = created/modified · **Tests** = tests written · **Result** = last executed result · **Notes / remaining**.

### PHASE-00 — Planning & architecture

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-00.01 | Environment inspection | COMPLETED | — | SESSION_LOG.md, PROJECT_PLAN.md §3 | n/a | n/a | D: 118 GB free; uv + Py 3.12.16; Docker 29.9.2 (2.8 GB RAM); Node missing; project dir created |
| TASK-00.02 | Requirements analysis, MVP vs optional | COMPLETED | 00.01 | PROJECT_PLAN.md §2 | n/a | n/a | Approved 2026-11-26 |
| TASK-00.03 | Architecture proposal | COMPLETED | 00.02 | ARCHITECTURE.md | n/a | n/a | Approved 2026-11-26 |
| TASK-00.04 | Database design | COMPLETED | 00.03 | DATABASE_DESIGN.md | n/a | n/a | Approved 2026-11-26 |
| TASK-00.05 | API design | COMPLETED | 00.03 | API_SPECIFICATION.md | n/a | n/a | Approved 2026-11-26 |
| TASK-00.06 | Plan, tracker, schedule, guidelines, ADRs, logs, README | COMPLETED | 00.02–00.05 | all root .md files, docs/*/README.md | n/a | n/a | Approved 2026-11-26 |
| TASK-00.07 | Plan approval | COMPLETED | 00.02–00.06 | — | — | — | Approved in chat 2026-11-26. P-1, P-2, P-3, P-5 unanswered → recommended defaults apply until changed |

### PHASE-01 — Dev environment & repo

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-01.01 | git init, ignore/attributes/editorconfig, initial commit | COMPLETED | 00.07 | .gitignore, .gitattributes, .editorconfig | `git check-ignore` matrix (18 must-ignore, 8 must-keep paths) | PASS 26/26 | Repo on `main`; repo-local personal identity (not the global work one); initial commit made. Push to personal GitHub tracked under B-04 |
| TASK-01.02 | Backend scaffold (uv, package tree, ruff/mypy/pytest) | COMPLETED | 01.01 | backend/pyproject.toml, backend/uv.lock, backend/.python-version, backend/app/**/__init__.py (23 packages), backend/tests/** layout, docs/development/README.md | tests/unit/test_project_layout.py (25 cases: Python 3.12 pin, 23 packages importable + documented, no namespace packages) | ruff check PASS · ruff format --check PASS (32 files) · mypy --strict PASS (31 files) · pytest 25 passed · negative check: stray dir without __init__.py → test FAILED as intended | Dev tools only (ruff 0.17.6, mypy 2.4.1, pytest 9.2.0); runtime deps added by the task that first needs them. uv `link-mode=copy` (cache on C:, project on D:) |
| TASK-01.03 | Settings + env profiles + `.env.example` | COMPLETED | 01.02 | backend/app/config/settings.py, backend/app/config/__init__.py, .env.example, backend/pyproject.toml (pydantic deps + mypy plugin), docs/development/README.md, DECISIONS.md (ADR-016) | tests/unit/test_settings.py (28 cases) | ruff PASS · format PASS · mypy --strict PASS · pytest 53 passed (28 new) · negative checks: var removed from .env.example → sync test FAILED; real env CORTEX_ENVIRONMENT=production with dev defaults → startup refused; CORTEX_DATABSE__HOST typo → rejected | LLM default model `gpt-4o-mini` is a placeholder until P-1 (provider) is answered |
| TASK-01.04 | Docker Compose dev infra with health checks | COMPLETED | 01.01 | infra/compose/docker-compose.yml; port defaults in backend/app/config/settings.py, .env.example, tests/unit/test_settings.py; README.md; docs/development/README.md; DECISIONS.md (ADR-017) | Manual verification script (no Python drivers yet; automated readiness tests come with TASK-01.06) | `up -d --wait` → postgres/redis/qdrant all healthy · PG 17.12 + pg_stat_statements loaded · Redis appendonly=yes, noeviction · Qdrant 1.20.2 /readyz 200, uid 1000 · health-check negatives (404 path, closed port) fail as intended · ports reachable on 127.0.0.1, NOT on LAN 192.168.1.9 · data survived down/up in all 3 volumes (markers removed) · backend suite 53 passed | First start failed: host 6379 held by the yenta-redis work container → moved Cortex to host ports 15432/16379/16333/16334 (ADR-017). Docker RAM still 2.8 GB (B-02) |
| TASK-01.05 | Backend Dockerfile + api service | NOT_STARTED | 01.02, 01.04 | | | | |
| TASK-01.06 | App factory + live/ready health | NOT_STARTED | 01.03 | | | | |
| TASK-01.07 | Structured logging + request id + redaction | NOT_STARTED | 01.06 | | | | |
| TASK-01.08 | Dev PowerShell scripts | NOT_STARTED | 01.04 | | | | |
| TASK-01.09 | GitHub Actions CI | NOT_STARTED | 01.02–01.07 | | | | Remote ready (Snsnehad/cortex) |

### PHASE-02 — Database & backend foundation

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-02.01 | Engine, session, Base, mixins | NOT_STARTED | 01.06 | | | | |
| TASK-02.02 | Alembic async + round-trip test | NOT_STARTED | 02.01 | | | | |
| TASK-02.03 | Users/orgs/memberships/refresh_tokens | NOT_STARTED | 02.02 | | | | |
| TASK-02.04 | KBs/documents/chunks/jobs (+composite FKs, partial uniques) | NOT_STARTED | 02.03 | | | | |
| TASK-02.05 | Conversations/messages/citations/usage_events | NOT_STARTED | 02.04 | | | | |
| TASK-02.06 | Exceptions + error envelope | NOT_STARTED | 01.06 | | | | |
| TASK-02.07 | Keyset pagination | NOT_STARTED | 02.01 | | | | |
| TASK-02.08 | Test harness (DB, rollback, factories, client) | NOT_STARTED | 02.02 | | | | |

### PHASE-03 — Auth & multi-tenancy

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-03.01 | Password hashing + policy | NOT_STARTED | 02.08 | | | | |
| TASK-03.02 | JWT access tokens | NOT_STARTED | 01.03 | | | | |
| TASK-03.03 | Rotating refresh tokens + reuse detection | NOT_STARTED | 02.03, 03.02 | | | | |
| TASK-03.04 | Auth endpoints | NOT_STARTED | 03.01–03.03 | | | | |
| TASK-03.05 | Current user, OrgContext, require_role | NOT_STARTED | 03.04 | | | | |
| TASK-03.06 | Org + member management | NOT_STARTED | 03.05 | | | | |
| TASK-03.07 | Redis rate limiter | NOT_STARTED | 03.04 | | | | |
| TASK-03.08 | Tenant-isolation test framework | NOT_STARTED | 03.05 | | | | |
| TASK-03.09 | Security model doc | NOT_STARTED | 03.06 | | | | |

### PHASE-04 — Upload & storage

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-04.01 | Storage protocol, local + S3 | NOT_STARTED | 01.03 | | | | |
| TASK-04.02 | KB CRUD | NOT_STARTED | 03.05, 02.04 | | | | |
| TASK-04.03 | Upload validation | NOT_STARTED | 04.01 | | | | |
| TASK-04.04 | Hashing + dedup | NOT_STARTED | 04.03 | | | | |
| TASK-04.05 | Upload endpoint (PENDING + QUEUED job) | NOT_STARTED | 04.02–04.04 | | | | |
| TASK-04.06 | Document list/get/status/download | NOT_STARTED | 04.05 | | | | |

### PHASE-05 — Background processing

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-05.01 | Celery app config | NOT_STARTED | 01.04, 02.01 | | | | |
| TASK-05.02 | Job state machine + atomic claim + lease | NOT_STARTED | 05.01, 02.04 | | | | |
| TASK-05.03 | Retry policy, transient vs permanent | NOT_STARTED | 05.02 | | | | |
| TASK-05.04 | Post-commit enqueue + reconciler | NOT_STARTED | 05.03 | | | | |
| TASK-05.05 | Reprocess + delete flows | NOT_STARTED | 05.04, 04.06 | | | | |
| TASK-05.06 | Worker/beat compose services + shutdown | NOT_STARTED | 05.01 | | | | |

### PHASE-06 — Extraction & chunking

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-06.01 | PDF parser | NOT_STARTED | 05.03 | | | | |
| TASK-06.02 | DOCX parser + zip-bomb guard | NOT_STARTED | 06.01 | | | | |
| TASK-06.03 | TXT/MD parser | NOT_STARTED | 06.01 | | | | |
| TASK-06.04 | Text cleaner with offset map | NOT_STARTED | 06.01 | | | | |
| TASK-06.05 | Structure-aware chunker | NOT_STARTED | 06.04 | | | | |
| TASK-06.06 | Pipeline stages 1–3 + chunk persistence | NOT_STARTED | 06.05, 05.04 | | | | |
| TASK-06.07 | Resource limits + error codes | NOT_STARTED | 06.06 | | | | |

### PHASE-07 — Embeddings & Qdrant

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-07.01 | Embedding providers (fastembed, openai-compat, fake) | NOT_STARTED | 01.03 | | | | |
| TASK-07.02 | Embedding profiles / versioning | NOT_STARTED | 07.01 | | | | |
| TASK-07.03 | Qdrant index wrapper (mandatory tenant filter) | NOT_STARTED | 07.02 | | | | |
| TASK-07.04 | Sparse BM25 encoder | NOT_STARTED | 07.01 | | | | |
| TASK-07.05 | Pipeline embed/index/finalize | NOT_STARTED | 07.03, 07.04, 06.06 | | | | |
| TASK-07.06 | PG↔Qdrant consistency reconciler | NOT_STARTED | 07.05 | | | | |
| TASK-07.07 | Ingestion benchmark | NOT_STARTED | 07.05 | | | | |
| TASK-07.08 | *(stretch)* KB reindex to new profile | NOT_STARTED | 07.05 | | | | Stretch |

### PHASE-08 — Dense & sparse retrieval

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-08.01 | Authorization → mandatory filter | NOT_STARTED | 07.03 | | | | |
| TASK-08.02 | Dense retriever | NOT_STARTED | 08.01 | | | | |
| TASK-08.03 | Sparse retriever | NOT_STARTED | 08.01 | | | | |
| TASK-08.04 | Hydration from PG | NOT_STARTED | 08.02 | | | | |
| TASK-08.05 | Debug search endpoint | NOT_STARTED | 08.04 | | | | |

### PHASE-09 — Hybrid & reranking

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-09.01 | Weighted RRF fusion | NOT_STARTED | 08.02, 08.03 | | | | |
| TASK-09.02 | Cross-encoder reranker | NOT_STARTED | 08.04 | | | | |
| TASK-09.03 | Configurable retrieval pipeline | NOT_STARTED | 09.01, 09.02 | | | | |
| TASK-09.04 | Stage timing + metrics | NOT_STARTED | 09.03 | | | | |

### PHASE-10 — RAG & LLM

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-10.01 | LLM providers (openai-compat, fake) | NOT_STARTED | 01.03 | | | | |
| TASK-10.02 | Context builder | NOT_STARTED | 09.03 | | | | |
| TASK-10.03 | Versioned prompts | NOT_STARTED | 10.02 | | | | |
| TASK-10.04 | Answer generator + citation mapper | NOT_STARTED | 10.01–10.03 | | | | |
| TASK-10.05 | Follow-up condensation | NOT_STARTED | 10.04 | | | | |
| TASK-10.06 | Cost table + usage events | NOT_STARTED | 10.04 | | | | |
| TASK-10.07 | Prompt-injection fixtures + manual run | NOT_STARTED | 10.04 | | | | Manual part needs B-03 |

### PHASE-11 — Chat & conversations

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-11.01 | Conversation CRUD | NOT_STARTED | 03.05, 02.05 | | | | |
| TASK-11.02 | Ask endpoint | NOT_STARTED | 11.01, 10.04 | | | | |
| TASK-11.03 | *(stretch)* SSE streaming | NOT_STARTED | 11.02 | | | | Stretch |
| TASK-11.04 | History + delete | NOT_STARTED | 11.02 | | | | |
| TASK-11.05 | Ask rate limits & caps | NOT_STARTED | 11.02, 03.07 | | | | |

### PHASE-12 — Frontend

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-12.01 | Node + Vite/Tailwind/shadcn scaffold | BLOCKED | B-01 | | | | Node not installed |
| TASK-12.02 | Typed API layer + refresh handling | NOT_STARTED | 12.01 | | | | |
| TASK-12.03 | Zustand stores | NOT_STARTED | 12.01 | | | | |
| TASK-12.04 | App shell | NOT_STARTED | 12.02, 12.03 | | | | |
| TASK-12.05 | Auth pages | NOT_STARTED | 12.04 | | | | |
| TASK-12.06 | Dashboard | NOT_STARTED | 12.04 | | | | |
| TASK-12.07 | KB pages | NOT_STARTED | 12.04 | | | | |
| TASK-12.08 | Upload + document management | NOT_STARTED | 12.07 | | | | |
| TASK-12.09 | Chat UI + citations + history | NOT_STARTED | 12.07 | | | | |
| TASK-12.10 | Analytics page | NOT_STARTED | 12.04, 13.01 | | | | |
| TASK-12.11 | Org/members/account settings | NOT_STARTED | 12.04 | | | | |
| TASK-12.12 | Frontend tests | NOT_STARTED | 12.02–12.11 | | | | |

### PHASE-13 — Analytics & monitoring

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-13.01 | Analytics endpoints | NOT_STARTED | 11.02 | | | | |
| TASK-13.02 | Prometheus metrics | NOT_STARTED | 09.04 | | | | |
| TASK-13.03 | *(stretch)* Grafana dashboards | NOT_STARTED | 13.02 | | | | Stretch |
| TASK-13.04 | Demo data seed script | NOT_STARTED | 13.01 | | | | |

### PHASE-14 — Testing & performance

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-14.01 | Evaluation corpus | NOT_STARTED | 07.05 | | | | |
| TASK-14.02 | ≥40 eval questions with gold labels | NOT_STARTED | 14.01 | | | | |
| TASK-14.03 | Retrieval eval (Recall@5, MRR, modes) | NOT_STARTED | 14.02, 09.03 | | | | |
| TASK-14.04 | Answer eval (citations, faithfulness, refusals) | NOT_STARTED | 14.03, 11.02 | | | | Needs B-03 |
| TASK-14.05 | Load tests | NOT_STARTED | 11.02 | | | | |
| TASK-14.06 | DB query analysis | NOT_STARTED | 14.05 | | | | |
| TASK-14.07 | Coverage gate in CI | NOT_STARTED | 01.09 | | | | |

### PHASE-15 — Security & production readiness

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-15.01 | OWASP API review | NOT_STARTED | 13.01 | | | | |
| TASK-15.02 | Dependency + secret scanning | NOT_STARTED | 01.09 | | | | |
| TASK-15.03 | Headers, CORS, hosts, body limits | NOT_STARTED | 15.01 | | | | |
| TASK-15.04 | File & prompt-injection risk review | NOT_STARTED | 10.07 | | | | |
| TASK-15.05 | Backup/restore runbook + drill | NOT_STARTED | 07.05 | | | | |

### PHASE-16 — Production deployment

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-16.01 | Deployment design + cost estimate | NOT_STARTED | 15.* | | | | Needs your approval |
| TASK-16.02 | Prod images, config, secrets, S3, RDS | NOT_STARTED | 16.01 | | | | |
| TASK-16.03 | Staging environment | NOT_STARTED | 16.02 | | | | |
| TASK-16.04 | Deploy workflow | NOT_STARTED | 16.03 | | | | |
| TASK-16.05 | Production deploy + rollback rehearsal | NOT_STARTED | 16.04 | | | | Separate approval |

### PHASE-17 — Documentation & portfolio

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-17.01 | README final + fresh-clone check | NOT_STARTED | all | | | | |
| TASK-17.02 | RAG + eval + troubleshooting docs | NOT_STARTED | 14.04 | | | | |
| TASK-17.03 | Diagrams, demo script, talking points | NOT_STARTED | all | | | | |
| TASK-17.04 | Tracker audit | NOT_STARTED | all | | | | |

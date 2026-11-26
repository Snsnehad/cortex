# Cortex — Task Tracker

> Source of truth for **status**. Definitions and completion conditions live in [PROJECT_PLAN.md](PROJECT_PLAN.md).
> A task is `COMPLETED` only when: implementation finished, relevant tests **executed and passing**, acceptance criteria met, docs updated.
> Statuses: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED` · `NEEDS_REVIEW`
> Last updated: 2026-11-26 (Session 1, after plan approval)

## Summary

| Phase | Name | Status | Done / Total |
|---|---|---|---|
| PHASE-00 | Planning & architecture | COMPLETED | 7 / 7 |
| PHASE-01 | Dev environment & repo | COMPLETED | 9 / 9 |
| PHASE-02 | Database & backend foundation | COMPLETED | 8 / 8 |
| PHASE-03 | Auth & multi-tenancy | IN_PROGRESS | 2 / 9 |
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

**Current task:** TASK-03.03 — rotating refresh tokens: generation, SHA-256 storage, rotation, reuse detection → family revoke, cookie settings.
**Next task:** TASK-03.04 (auth endpoints).

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
| TASK-01.05 | Backend Dockerfile + api service | COMPLETED | 01.02, 01.04, 01.06 | backend/Dockerfile, backend/.dockerignore, infra/compose/docker-compose.yml (api service, "app" profile, storage_data volume), docs/development/README.md, DECISIONS.md (ADR-019) | Manual container verification (CI build check comes with TASK-01.09) | build OK (cold 69 s, code-only 13 s), image 266 MB · `--profile app up --wait` → api healthy · uid 10001 · pytest/mypy/uv/ruff/tests absent · root FS read-only, /data/storage + /tmp writable · CapEff 0 · no server banner · /ready 200 via service names · redis stopped 25 s → /ready 503, container healthy, 0 restarts → recovered 200 · no secrets in image layers · 0 error log lines during outage after ADR-019 | Done after 01.06 (reordered). Found: uvloop logs unretrieved-future errors on cancelled DNS lookups → switched container to `--loop asyncio` (ADR-019) |
| TASK-01.06 | App factory + live/ready health | COMPLETED | 01.03 | app/main.py, app/api/v1/{router,health}.py, app/services/health.py, app/dependencies/{health,settings}.py, app/schemas/health.py, tests/conftest.py; settings + .env.example (127.0.0.1 defaults); README, docs/development, DECISIONS (ADR-018) | tests/unit/test_health_service.py (7), tests/unit/test_health_api.py (6), tests/integration/test_health_ready.py (2) | ruff PASS · mypy --strict PASS · pytest 68 passed (15 new, incl. 2 integration vs live stack) · manual uvicorn run: all up 200 → redis stopped 503 (timeout) → restarted 200 → postgres stopped 503 → restarted 200; /live stays 200 | Found + fixed 2 Windows issues (ADR-018): localhost→127.0.0.1 (10 s → 24 ms), selector loop for psycopg. Readiness runs ~2 s when a port is refused on Windows (bounded by timeout) |
| TASK-01.07 | Structured logging + request id + redaction | COMPLETED | 01.06 | app/core/logging.py, app/middleware/request_context.py, app/main.py, app/config/settings.py (log_format), .env.example, docs/development/README.md | tests/unit/test_logging.py (39 incl. parametrised), tests/unit/test_request_context.py (12) | ruff PASS · mypy PASS · pytest 119 passed · container: 0 non-JSON lines (uvicorn included), client X-Request-ID echoed + logged, generated id on 404, query-string api_key absent from logs, no uvicorn.access duplicates, health probes not at INFO, no ANSI color_message | Fixed during task: handler now resolves sys.stdout per record (stale closed stream otherwise); Redis-style empty-user URL credentials redacted; uvicorn color_message dropped. Unhandled exceptions may also be logged by uvicorn without request_id until TASK-02.06 |
| TASK-01.08 | Dev PowerShell scripts | COMPLETED | 01.04 | scripts/dev.ps1, scripts/dev.cmd, README.md (local setup), docs/development/README.md | Manual run of every command via dev.cmd from an unrelated folder (PS 5.1) | help OK · unknown command → exit 2 · ps OK · logs redis OK · lint OK · test -m "not integration" → 117 passed, 2 deselected · test -p no:cacheprovider -k settings → 29 passed (-p not captured) · test -k nonexistent → pytest exit 5 → script exit 1 · check → 119 passed · up -App → api healthy, ready 200 · down → exit 0, 0 containers · up → data marker survived · api on host → ready 200, process tree stopped · format → no changes · reset: wrong answer and EOF both abort with exit 1, volumes intact · .ps1 blocked under Restricted, dev.cmd works | `migrate` deferred to TASK-02.02 (Alembic). Real reset (down -v) intentionally not executed (destructive) |
| TASK-01.09 | GitHub Actions CI | COMPLETED | 01.02–01.07 | .github/workflows/ci.yml, README.md (badge), docs/development/README.md | Workflow itself; actionlint 1.7.12 (incl. shellcheck) | actionlint PASS (after fixing 2 SC2034 warnings) · PR #1 run 45810619558 on db60f78: Backend PASS (119 passed on Linux incl. integration vs service containers), Docker image PASS (healthy, uid 10001, logs all JSON) · older run cancelled by concurrency as designed · PR #1 merged (fast-forward) · main push run 45812054140: SUCCESS (both jobs) | Auto-fix could not be enabled (Cortex repo is outside this session's folder); CI run time ~45 s |

### PHASE-02 — Database & backend foundation

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-02.01 | Engine, session, Base, mixins | COMPLETED | 01.06 | app/database/{base,session}.py, app/dependencies/database.py, app/utils/ids.py, app/main.py (engine on app.state, lifespan dispose), docs/development/README.md, TECH_STACK.md | tests/unit/test_ids.py (6), tests/unit/test_database_base.py (6), tests/integration/test_database_session.py (9) | ruff · mypy · pytest 140 passed · against PostgreSQL: UUIDv7 + server timestamps, updated_at readable after UPDATE (eager_defaults), failed request rolled back, 100 requests (10 failing) → 0 checked out, 40 concurrent → within pool limits, lifespan disposes pool · pg_stat_activity: 0 cortex connections left, 0 leftover test tables | SQLAlchemy 2.1.9 (current stable; plan said 2.0, same API). Own UUIDv7 (Python 3.12 lacks uuid.uuid7) |
| TASK-02.02 | Alembic async + round-trip test | COMPLETED | 02.01 | backend/alembic.ini, backend/migrations/{env.py, script.py.mako, README, versions/.gitkeep}, app/models/__init__.py (registry), backend/Dockerfile (ships migrations), backend/pyproject.toml (alembic; mypy on env.py), scripts/dev.ps1 (migrate, revision), docs/development/README.md | tests/integration/test_migrations.py (6: no URL in ini, single head, round trip, stairway, models/migrations drift, offline SQL) | ruff · mypy · pytest 146 passed · probe-model proof: drift test FAILED without a migration → `dev revision` produced a pk_/uq_-named, ruff-formatted file → all 6 PASSED → broken downgrade() made round trip + stairway FAIL → `dev migrate` / `migrate -1` on dev DB OK → probe fully removed · `alembic upgrade head` inside the read-only container OK · 0 scratch DBs left | Alembic 1.20.1. No revisions yet (first real one in TASK-02.03); dev DB has an empty alembic_version table |
| TASK-02.03 | Users/orgs/memberships/refresh_tokens | COMPLETED | 02.02 | app/models/{user,organization,refresh_token,__init__}.py, migrations/versions/20261126_1203-05181d5746d1_create_users_organizations_memberships_refresh_tokens.py, tests/integration/conftest.py (shared scratch + migrated DB), tests/integration/test_migrations.py (uses conftest), DATABASE_DESIGN.md | tests/integration/test_identity_models.py (14), tests/unit/test_identity_models_unit.py (7) | ruff · mypy · pytest 167 passed · migration safety net now on a real revision (round trip, stairway, drift all pass) · constraint names verified via PostgreSQL diag: uq_users_email_lower, uq_organizations_slug, uq_organization_memberships_organization_id_user_id, ck_organization_memberships_role, uq_refresh_tokens_token_hash, fk_* · ON DELETE CASCADE / SET NULL verified · lazy="raise" verified · regression proof: reintroducing lower('email') makes test_different_emails_can_both_register FAIL while the case-insensitive test still passes | Bug caught in hand review of the autogenerated migration: func.lower("email") produced an index on a constant (one user max); fixed by building the index from User.email. Dev DB migrated to 05181d5746d1 |
| TASK-02.04 | KBs/documents/chunks/jobs (+composite FKs, partial uniques) | COMPLETED | 02.03 | app/models/{knowledge_base,document,processing_job,__init__}.py, migrations/versions/20261126_1212-6228847616cc_create_knowledge_bases_documents_chunks_jobs.py, tests/integration/{conftest.py (db_engine, session_factory: truncate all tables), db_helpers.py}, tests/integration/test_identity_models.py (shared fixtures), DATABASE_DESIGN.md | tests/integration/test_document_models.py (27), tests/unit/test_document_models_unit.py (4) | ruff · mypy · pytest 198 passed · migration safety net (round trip, stairway, drift) passes on 2 revisions · verified in PostgreSQL: composite FKs, partial unique WHERE clauses, lower(name), DESC indexes, CHECK names · concurrent identical uploads: exactly 1 of 3 wins · mutation proof: single-column KB FK lets a cross-tenant document in (test FAILS); non-unique active-job index lets a 2nd active job in (test FAILS); restored byte-identical | Design strengthened: chunks and jobs now have composite tenant FKs instead of trusting the worker (DATABASE_DESIGN §4). Chunk id has no default by design. Dev DB at 6228847616cc |
| TASK-02.05 | Conversations/messages/citations/usage_events | COMPLETED | 02.04 | app/models/{conversation,usage_event,__init__}.py, app/database/base.py (CreatedAtMixin), migrations/versions/20261126_1222-ba17b2b568e9_create_conversations_messages_citations_usage_events.py, tests/unit/test_database_base.py, DATABASE_DESIGN.md, DECISIONS.md (ADR-020) | tests/integration/test_chat_models.py (16) | ruff · mypy · pytest 214 passed · migration safety net passes on 3 revisions · drift test caught Float(24) vs REAL → fixed in model and migration · exact Decimal sums (10×0.1 + 0.000123 = 1.000123), micro-dollar rounding · mutation proof: plain user FK lets a non-member own a conversation (test FAILS); CASCADE citations vanish with their document (test FAILS); restored byte-identical | Design strengthened: conversation ownership tied to membership (ADR-020, open to veto). Dev DB at ba17b2b568e9 |
| TASK-02.06 | Exceptions + error envelope | COMPLETED | 01.06 | app/core/errors.py, app/api/errors.py, app/schemas/errors.py, app/middleware/request_context.py (generic 500 inside the request context, no re-raise), app/main.py, docs/development/README.md | tests/unit/test_error_handling.py (27) | ruff · mypy · pytest 241 passed · every domain error → status + envelope; Retry-After / WWW-Authenticate; 404 / 405 (+Allow) / route HTTPException / 422 envelopes; 422 never echoes the submitted password; 500 leaks no exception text, URL or traceback · real uvicorn: 1 structured traceback with request_id, 0 uvicorn duplicates, 0 plain-text tracebacks, secret redacted | Closes the TASK-01.07 known gap (duplicate uvicorn traceback). Failures after the response started are logged once and re-raised so the server aborts the stream (tested) |
| TASK-02.07 | Keyset pagination | COMPLETED | 02.01 | app/core/pagination.py (signed CursorCodec, InvalidCursorError), app/repositories/pagination.py (KeysetOrder, apply_keyset, fetch_page, PageRequest), app/schemas/pagination.py (Page[T]), app/dependencies/pagination.py (PageParams, CursorCodecDep), app/core/errors.py + app/api/errors.py (BadRequestError → 400), app/main.py (codec on app.state), docs/development/README.md | tests/unit/test_pagination_cursor.py (21), tests/integration/test_pagination.py (14) | ruff · mypy · pytest 276 passed · 45 rows with identical created_at paged exactly once in order · inserts between pages: no duplicates/skips (OFFSET would repeat 4) · cursor row deleted: next page correct · tampered/forged/foreign-scope/foreign-secret/garbage/over-long cursors → 400 INVALID_CURSOR · limit 0/101/abc → 422 · EXPLAIN ANALYZE on 20k rows (rolled-back txn): page 1 and ~9,000-deep page each read 22 index rows | Cursors signed (HMAC-SHA256, derived key), not encrypted. Follow-up noted for TASK-14.06: add id DESC to the created_at indexes if tie groups get large (incremental sort observed) |
| TASK-02.08 | Test harness (DB, rollback, factories, client) | COMPLETED | 02.02 | tests/conftest.py (auto `integration` marker by folder), tests/integration/conftest.py (per-test pooled engine, one-round-trip DELETE cleanup, test_settings / api_app / api_client), tests/integration/factories.py, backend/pyproject.toml (pytest-randomly), docs/testing/README.md | tests/integration/test_harness.py (5) | ruff · mypy · pytest 281 passed · 3 full random-order runs passed · same seed → same order, different seeds → different orders · unit tests pass with PostgreSQL stopped (190 in ~8 s) · api_client proven to use the scratch DB · measured cleanup: TRUNCATE 60 ms vs DELETE 29 ms; NullPool new connection 33 ms per session → integration suite ~43 s → ~28 s | Decision: real commits + DELETE cleanup instead of savepoint rollback, because concurrency tests need separate transactions (documented with measurements) |

### PHASE-03 — Auth & multi-tenancy

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-03.01 | Password hashing + policy | COMPLETED | 02.08 | app/core/passwords.py, backend/pyproject.toml (pwdlib[argon2] 0.4.2, argon2-cffi 25.1.0), DECISIONS.md (ADR-021), docs/development/README.md | tests/unit/test_passwords.py (27) | ruff · mypy · pytest 308 passed · Argon2id m=19 MiB t=2 p=1 (hash format asserted) · salted · rehash-on-login when parameters change (and never on a wrong password) · NFKC: fullwidth and combining forms verify · corrupt/foreign hashes → invalid, no crash · dummy_verify within 0.5–2× of a real verify · async hashing keeps the event loop responsive · NIST-style policy incl. boundaries 12/128 · measured ≈53 ms per hash (default ≈116 ms) | Breached-password check (HIBP) noted as a possible enhancement. Fixed in passing: invisible/look-alike Unicode in test source now written as \u escapes |
| TASK-03.02 | JWT access tokens | COMPLETED | 01.03 | app/core/tokens.py (AccessTokenService, TokenExpiredError, InvalidTokenError), app/main.py (app.state.access_tokens), backend/pyproject.toml (pyjwt 2.15.1), docs/development/README.md | tests/unit/test_access_tokens.py (30) | ruff · mypy · pytest 338 passed · round trip; payload has only sub + protocol claims; unique jti · expired → TOKEN_EXPIRED; 10 s skew tolerated; future iat rejected · tampered payload/signature, other secret, raw (underived) secret, alg=none, correctly signed HS512 → INVALID_TOKEN · wrong typ/aud/iss, non-UUID sub, missing jti/typ/aud/exp → INVALID_TOKEN · mutation: disabling the typ check lets a typ=refresh token through (test fails); restored byte-identical | Dedicated derived signing key. Roles intentionally not in the token (checked per request) |
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
| TASK-14.06 | DB query analysis | NOT_STARTED | 14.05 | | | | Input from TASK-02.07: keyset pages use (…, created_at DESC) indexes plus an incremental sort on id; consider (…, created_at DESC, id DESC) if large tie groups appear |
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

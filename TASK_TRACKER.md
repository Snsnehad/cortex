# Cortex — Task Tracker

> Source of truth for **status**. Definitions and completion conditions live in [PROJECT_PLAN.md](PROJECT_PLAN.md).
> A task is `COMPLETED` only when: implementation finished, relevant tests **executed and passing**, acceptance criteria met, docs updated.
> Statuses: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED` · `NEEDS_REVIEW`
> Last updated: 2026-11-27 (Session 2, after TASK-04.05)

## Summary

| Phase | Name | Status | Done / Total |
|---|---|---|---|
| PHASE-00 | Planning & architecture | COMPLETED | 7 / 7 |
| PHASE-01 | Dev environment & repo | COMPLETED | 9 / 9 |
| PHASE-02 | Database & backend foundation | COMPLETED | 8 / 8 |
| PHASE-03 | Auth & multi-tenancy | COMPLETED | 9 / 9 |
| PHASE-04 | Upload & storage | IN_PROGRESS | 5 / 6 |
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

**Current task:** TASK-04.06 — documents: list per KB (filter `status`, search `q`, paginated), get, job status, download (stream or presigned), delete (uploader or ADMIN, ADR-010).
**Next task:** TASK-05.01 (Phase 5: Celery worker + beat).

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
| TASK-02.05 | Conversations/messages/citations/usage_events | COMPLETED | 02.04 | app/models/{conversation,usage_event,__init__}.py, app/database/base.py (CreatedAtMixin), migrations/versions/20261126_1222-ba17b2b568e9_create_conversations_messages_citations_usage_events.py, tests/unit/test_database_base.py, DATABASE_DESIGN.md, DECISIONS.md (ADR-020) | tests/integration/test_chat_models.py (16) | ruff · mypy · pytest 214 passed · migration safety net passes on 3 revisions · drift test caught Float(24) vs REAL → fixed in model and migration · exact Decimal sums (10×0.1 + 0.000123 = 1.000123), micro-dollar rounding · mutation proof: plain user FK lets a non-member own a conversation (test FAILS); CASCADE citations vanish with their document (test FAILS); restored byte-identical | Design strengthened: conversation ownership tied to membership (ADR-020, confirmed final 2026-11-27). Dev DB at ba17b2b568e9 |
| TASK-02.06 | Exceptions + error envelope | COMPLETED | 01.06 | app/core/errors.py, app/api/errors.py, app/schemas/errors.py, app/middleware/request_context.py (generic 500 inside the request context, no re-raise), app/main.py, docs/development/README.md | tests/unit/test_error_handling.py (27) | ruff · mypy · pytest 241 passed · every domain error → status + envelope; Retry-After / WWW-Authenticate; 404 / 405 (+Allow) / route HTTPException / 422 envelopes; 422 never echoes the submitted password; 500 leaks no exception text, URL or traceback · real uvicorn: 1 structured traceback with request_id, 0 uvicorn duplicates, 0 plain-text tracebacks, secret redacted | Closes the TASK-01.07 known gap (duplicate uvicorn traceback). Failures after the response started are logged once and re-raised so the server aborts the stream (tested) |
| TASK-02.07 | Keyset pagination | COMPLETED | 02.01 | app/core/pagination.py (signed CursorCodec, InvalidCursorError), app/repositories/pagination.py (KeysetOrder, apply_keyset, fetch_page, PageRequest), app/schemas/pagination.py (Page[T]), app/dependencies/pagination.py (PageParams, CursorCodecDep), app/core/errors.py + app/api/errors.py (BadRequestError → 400), app/main.py (codec on app.state), docs/development/README.md | tests/unit/test_pagination_cursor.py (21), tests/integration/test_pagination.py (14) | ruff · mypy · pytest 276 passed · 45 rows with identical created_at paged exactly once in order · inserts between pages: no duplicates/skips (OFFSET would repeat 4) · cursor row deleted: next page correct · tampered/forged/foreign-scope/foreign-secret/garbage/over-long cursors → 400 INVALID_CURSOR · limit 0/101/abc → 422 · EXPLAIN ANALYZE on 20k rows (rolled-back txn): page 1 and ~9,000-deep page each read 22 index rows | Cursors signed (HMAC-SHA256, derived key), not encrypted. Follow-up noted for TASK-14.06: add id DESC to the created_at indexes if tie groups get large (incremental sort observed) |
| TASK-02.08 | Test harness (DB, rollback, factories, client) | COMPLETED | 02.02 | tests/conftest.py (auto `integration` marker by folder), tests/integration/conftest.py (per-test pooled engine, one-round-trip DELETE cleanup, test_settings / api_app / api_client), tests/integration/factories.py, backend/pyproject.toml (pytest-randomly), docs/testing/README.md | tests/integration/test_harness.py (5) | ruff · mypy · pytest 281 passed · 3 full random-order runs passed · same seed → same order, different seeds → different orders · unit tests pass with PostgreSQL stopped (190 in ~8 s) · api_client proven to use the scratch DB · measured cleanup: TRUNCATE 60 ms vs DELETE 29 ms; NullPool new connection 33 ms per session → integration suite ~43 s → ~28 s | Decision: real commits + DELETE cleanup instead of savepoint rollback, because concurrency tests need separate transactions (documented with measurements) |

### PHASE-03 — Auth & multi-tenancy

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-03.01 | Password hashing + policy | COMPLETED | 02.08 | app/core/passwords.py, backend/pyproject.toml (pwdlib[argon2] 0.4.2, argon2-cffi 25.1.0), DECISIONS.md (ADR-021), docs/development/README.md | tests/unit/test_passwords.py (27) | ruff · mypy · pytest 308 passed · Argon2id m=19 MiB t=2 p=1 (hash format asserted) · salted · rehash-on-login when parameters change (and never on a wrong password) · NFKC: fullwidth and combining forms verify · corrupt/foreign hashes → invalid, no crash · dummy_verify within 0.5–2× of a real verify · async hashing keeps the event loop responsive · NIST-style policy incl. boundaries 12/128 · measured ≈53 ms per hash (default ≈116 ms) | Breached-password check (HIBP) noted as a possible enhancement. Fixed in passing: invisible/look-alike Unicode in test source now written as \u escapes |
| TASK-03.02 | JWT access tokens | COMPLETED | 01.03 | app/core/tokens.py (AccessTokenService, TokenExpiredError, InvalidTokenError), app/main.py (app.state.access_tokens), backend/pyproject.toml (pyjwt 2.15.1), docs/development/README.md | tests/unit/test_access_tokens.py (30) | ruff · mypy · pytest 338 passed · round trip; payload has only sub + protocol claims; unique jti · expired → TOKEN_EXPIRED; 10 s skew tolerated; future iat rejected · tampered payload/signature, other secret, raw (underived) secret, alg=none, correctly signed HS512 → INVALID_TOKEN · wrong typ/aud/iss, non-UUID sub, missing jti/typ/aud/exp → INVALID_TOKEN · mutation: disabling the typ check lets a typ=refresh token through (test fails); restored byte-identical | Dedicated derived signing key. Roles intentionally not in the token (checked per request) |
| TASK-03.03 | Rotating refresh tokens + reuse detection | COMPLETED | 02.03, 03.02 | app/core/refresh_tokens.py, app/api/auth_cookies.py, app/repositories/refresh_tokens.py, app/services/sessions.py, DECISIONS.md (ADR-022), docs/development/README.md | tests/unit/test_refresh_token_primitives.py (6), tests/integration/test_refresh_sessions.py (13) | ruff · mypy · pytest 357 passed · only SHA-256 stored; user agent truncated · rotation within family, chain leaves 1 active · unknown → INVALID_REFRESH_TOKEN, expired → REFRESH_TOKEN_EXPIRED, inactive user → refused + family revoked · reuse revokes the newest token too (checked from a fresh session), other device unaffected, logged without tokens · concurrent double use: exactly 1 succeeds, then family revoked · logout idempotent · cookie attributes asserted · 3 random seeds green · mutations: no reuse detection → 5 fail; no commit before reuse error → 2 fail | My test bug: log assertion relied on an earlier test configuring JSON logging (order-dependent); now configures it itself |
| TASK-03.04 | Auth endpoints | COMPLETED | 03.01–03.03 | app/api/v1/auth.py, app/api/v1/router.py, app/services/auth.py (AuthService, slugify, InvalidCredentialsError), app/dependencies/auth.py (CurrentUser, AuthServiceDep), app/schemas/auth.py, app/repositories/{users,organizations}.py, app/repositories/refresh_tokens.py (get_by_hash), app/main.py (app.state.password_hasher), backend/pyproject.toml (email-validator 2.4.0), API_SPECIFICATION.md, docs/development/README.md | tests/integration/test_auth_api.py (25), tests/unit/test_slugify.py (9) | ruff · mypy · pytest 391 passed · auth API tests green under 3 random seeds · register atomic + race-safe (3 concurrent → 201/409/409), slugs unique · login failures identical in body and timing · outdated hash upgraded on login · deactivated user locked out immediately · CSRF header enforced · reuse ends session · logout clears cookie · password change keeps this session, ends others · mutations: no dummy verify → unknown email 12.8 ms vs 56.8 ms (test fails); no is_active check → deactivated login 200 (test fails) · real server + curl cookie jar: cookie HttpOnly, sent only under /api/v1/auth | get_current_user implemented here (minimal); OrgContext/require_role in 03.05. Fixed: slug "x-s-workspace" from apostrophes. Note: a failed refresh does not clear the cookie (error responses are built by the handler); the client goes to login, which overwrites it |
| TASK-03.05 | Current user, OrgContext, require_role | COMPLETED | 03.04 | app/dependencies/orgs.py (OrgContext, OrgContextDep, require_role, AdminContext, OwnerContext, OrganizationNotFoundError), app/repositories/organizations.py (get_membership), app/dependencies/auth.py (binds user_id to logs), app/middleware/request_context.py (removes request-scoped log keys), docs/development/README.md | tests/integration/test_org_context.py (19) | ruff · mypy · pytest 410 passed · 3 random seeds green · full 3×3 role matrix · member of another org → 404 on member/admin/owner routes · unknown vs foreign org bodies identical · anonymous → 401 for existing and unknown orgs · deactivated → 401 · role change and removal apply to the next request with the same token · logs carry user_id/organization_id without leaking into the next request · mutations: membership looked up by user only → 4 cross-tenant tests fail (role matrix still passed!); log keys not removed → leak test fails | CurrentUser itself was built in 03.04. Insight recorded: only cross-tenant tests catch a missing org filter, which motivates TASK-03.08 |
| TASK-03.06 | Org + member management | COMPLETED | 03.05 | app/api/v1/organizations.py, app/services/organizations.py (OrganizationService, slugify moved here), app/schemas/organizations.py, app/core/tenancy.py (OrgContext moved here; breaks an import cycle), app/dependencies/orgs.py (re-exports), app/repositories/organizations.py (lock, count_owners, paginated queries), app/services/auth.py (uses add_with_owner), app/api/v1/router.py, API_SPECIFICATION.md, docs/development/README.md, docs/testing/README.md | tests/integration/test_organizations_api.py (36), tests/unit/test_slugify.py (import path) | ruff · mypy · pytest 446 passed · org tests green under 6 random seeds · list/create/get/rename (slug kept) · members list paginated, private to members · add: role rules, USER_NOT_FOUND (unknown/inactive), ALREADY_MEMBER · role-change and removal permission matrices · LAST_OWNER · ADR-020 cascade verified · mutations: no last-owner check → 2 fail; **no lock and stale role initially SURVIVED** (gather does not overlap at the critical point) → tests rewritten with a barrier on count_owners and a hook inside lock → both mutations now fail 3/3 | Lesson recorded in docs/testing: prove concurrency tests with mutations. Enumeration note: POST /members reveals whether an email has an account to ADMINs (accepted; rate limiting in 03.07) |
| TASK-03.07 | Redis rate limiter | COMPLETED | 03.04 | app/core/rate_limit.py (Lua sliding-window log, RateLimitPolicy, RateLimitUnavailableError), app/dependencies/rate_limit.py (RateLimitGuard, IP dependencies, email normalization), app/config/settings.py (RateLimitSettings; deployed envs require enabled), app/main.py (Redis client on app.state, closed on shutdown), app/api/v1/auth.py (register 5/h/IP; login 10/min/IP + 5/min/email), .env.example, tests/integration/conftest.py (per-test key prefix, high limits), API_SPECIFICATION.md, DECISIONS.md (ADR-011 implementation), docs/development/README.md | tests/unit/test_rate_limit_unit.py (14), tests/integration/test_rate_limit.py (11), tests/unit/test_settings.py (+1) | ruff · mypy · pytest 472 passed · exact limit then 429 + Retry-After · identities/policies separate · sliding window, rejections not recorded · 30 concurrent vs limit 5 → exactly 5 · keys hashed + TTL · per-account limit ignores case/spaces · Redis down → 503 and nothing created · disabled → Redis never touched · logs carry a hash prefix only · mutations: off-by-one, no email normalization, fail-open default, email limit removed, register limit removed, raw identity in key → all fail; **recording rejections initially SURVIVED** (the key TTL expired the evidence) → test rewritten, now fails; non-atomic check-then-add → concurrency test fails 3/3 | Client IP = TCP peer; trusted-proxy handling (X-Forwarded-For) is configured in Phase 16. An over-limit login still consumes the per-IP budget (accepted). |
| TASK-03.08 | Tenant-isolation test framework | COMPLETED | 03.05 | tests/integration/tenancy.py (World: two orgs × owner/admin/member + outsider; ISOLATION_CASES; NOT_TENANT_SCOPED with reasons; whole-database snapshot), docs/testing/README.md | tests/integration/test_tenant_isolation.py (17: registry completeness, no hidden routes, 6 foreign-org probes × 3 attackers, 2 foreign-resource probes, 6 positive controls) | ruff · mypy · pytest 489 passed · every API operation (17, from the OpenAPI schema) classified · foreign org → 404 identical to an unknown org for B's owner/admin/member, database unchanged · A's member addressed through B's org → 404 identical to an unknown id, unchanged · A's owner succeeds with the same requests · mutations, all killed: membership lookup ignores org (8 fail), member target looked up in any org (2), side effect before the 404 (6, snapshot), foreign 404 worded differently (6), registry case deleted (1), new org route without a case (1) | FastAPI 0.165 keeps included routers as private lazy `_IncludedRouter` nodes, so routes are discovered from the OpenAPI schema; `include_in_schema=False` under app/ is forbidden by a test so no route can hide. New resources (KBs, documents, conversations) extend `World.resources_a` as they arrive |
| TASK-03.09 | Security model doc | COMPLETED | 03.06 | docs/architecture/security-model.md (assets, trust boundaries, STRIDE table, passwords, access/refresh token lifecycle, tenancy, RBAC, rate limits, logging/errors/secrets, container, 10 known gaps with owners), ARCHITECTURE.md §6–7 (link; isolation suite and JWT claims brought up to date), docs/architecture/README.md | n/a (documentation) | Every claim checked against code: constants (Argon2 m/t/p, JWT claims, leeway, key context, TTLs, cookie attributes, rate limits), test names cited, all 21 file links resolve, port bindings, planned-task owners (TASK-15.01/15.03, Phase 8/12/16) matched to PROJECT_PLAN | Found while verifying: refresh tokens get a fresh 14-day expiry on every rotation, so active sessions have no absolute lifetime (G-2, accepted for now); `cors_origins` is validated but no CORS middleware is wired (G-6, safe default until TASK-15.03) |

### PHASE-04 — Upload & storage

| ID | Description | Status | Dep | Files | Tests | Result | Notes / remaining |
|---|---|---|---|---|---|---|---|
| TASK-04.01 | Storage protocol, local + S3 | COMPLETED | 01.03 | app/storage/base.py (StorageBackend protocol, StoredObject, key validation, document_key, ContentMeter, read_all, content_disposition, errors incl. 503 STORAGE_UNAVAILABLE), app/storage/local.py (LocalStorage), app/storage/s3.py (S3Storage, create_s3_client), app/storage/__init__.py (create_storage), app/config/settings.py (s3_sse, s3_kms_key_id, presigned_url_ttl_seconds), .env.example, pyproject.toml/uv.lock (boto3; dev: moto[s3,server], types-boto3[s3]), docs/development/README.md | tests/unit/test_storage.py (75: 13 contract tests × local and S3, key validation, local confinement, S3 encryption/presign/error mapping, download names) | ruff · mypy · pytest 562 passed, 1 skipped (symlink test needs Developer Mode on Windows; runs on Linux CI) · S3 tested over real HTTP against moto's server, no Docker · size + SHA-256 computed in the single streaming pass · oversized/failed upload leaves no object and keeps the previous one · local: two independent walls (key validation; resolved path inside root, tested with the first wall switched off), Windows device names refused, atomic temp-file + rename · S3: SSE on every put, nothing uploaded before the limit check, presigned GET fetched over HTTP with forced attachment, tampered URL → 403 · network/5xx/throttling → 503, AccessDenied stays a 500 · mutations, all 12 killed (limit off-by-one, no containment, no device check, no temp cleanup, write in place, S3 no limit, no SSE, not-found unmapped, 5xx not retryable, network unmapped, inline disposition, read_all not closing the stream) | Found by the tests: abandoning a stream early (over-limit read, failed upload) left the file/response open until GC; streams are now closed explicitly (`aclosing`), and the protocol returns AsyncGenerator so that is type-checked. Storage is not on app.state yet: wired by the upload endpoint (04.03–04.05), so app start does not create folders nobody uses. |
| TASK-04.02 | KB CRUD | COMPLETED | 03.05, 02.04 | app/api/v1/knowledge_bases.py, app/services/knowledge_bases.py (KnowledgeBaseService; KNOWLEDGE_BASE_NOT_FOUND / KNOWLEDGE_BASE_NAME_TAKEN), app/repositories/knowledge_bases.py (active_query, get_active by (org, id), grouped stats), app/schemas/knowledge_bases.py, app/api/v1/router.py, app/config/settings.py (EmbeddingSettings.profile), API_SPECIFICATION.md, docs/development/README.md, docs/testing/README.md | tests/integration/test_knowledge_bases_api.py (41), tests/integration/tenancy.py (+5 cases, org-A KB in the world), tests/integration/api_helpers.py (Api/team/code shared; `api` fixture moved to conftest) | ruff · mypy · pytest 616 passed, 1 skipped · create with defaults/trim/profile · chunking validation (schema and merged values) · names unique per org ignoring case, race → exactly one 201 · list newest first, paginated, members allowed · stats exclude DELETING documents, same in list and detail · listing 1 or 5 KBs runs the same number of SQL statements · partial PATCH, description clearing, chunking notice · soft delete: 202, hidden, name freed, documents kept for cleanup, concurrent deletes → 202 + 404 · 15-case role matrix · isolation: 30 cases pass · mutations, all 10 killed (read ignores org [caught only by the isolation framework], deleted shown, DELETING counted, N+1 stats, overlap = size allowed [DB CHECK still refused it, as a 500], name conflict unmapped, delete ignores org, delete twice, members may create, PATCH not partial) | Found by the isolation framework's positive controls: `Result.tuples()` is deprecated in SQLAlchemy 2.1 (warnings are errors) and made three routes 500 for the legitimate owner; fixed. Cleanup of deleted KBs is a Phase 5 job (it finds `deleted_at IS NOT NULL`). |
| TASK-04.03 | Upload validation | COMPLETED | 04.01 | app/uploads/receive.py (receive_upload: python-multipart push parser over request.stream(); FILE_TOO_LARGE / INVALID_UPLOAD), app/uploads/detect.py (detect_file_type: PDF/DOCX/UTF-8 text from bytes; UNSUPPORTED_FILE_TYPE with reason, FILE_EMPTY), app/uploads/filenames.py (sanitize_filename, extension_of), app/uploads/__init__.py (detect_upload), pyproject.toml/uv.lock (python-multipart 0.0.34), DECISIONS.md (ADR-023), API_SPECIFICATION.md, TECH_STACK.md, ARCHITECTURE.md, docs/architecture/security-model.md (G-10), docs/development/README.md | tests/unit/test_upload_detect.py (56), tests/unit/test_upload_receive.py (32) | ruff · mypy · pytest 704 passed, 1 skipped · declared Content-Length too big → 413 with **0** body chunks read · chunked 64 MiB upload vs 1 MiB limit → 413 after ≤ 18 of 1024 chunks · lying Content-Length and multipart epilogue junk capped the same way · exact limit accepted (spills to disk, content intact) · one file part only; extra/missing/second parts, wrong content type, malformed and truncated bodies → 400 · spool closed on every failure · PDF by header; DOCX by content types + word/document.xml; docm/dotx/xlsx, macros, encrypted entries, corrupt/truncated zips, declared bombs, too many entries → 415 with reason · UTF-8 incl. BOM and a character split across the 64 KiB read; Latin-1, NUL, control chars, renamed .exe/ELF/PNG → 415 · empty → 422 · filenames: paths, RTLO/zero-width, NFC, trailing dots, length with extension kept · mutations, all 13 killed | Found while writing tests: (1) tabs/newlines in filenames were deleted instead of becoming spaces (fixed); (2) Python's zipfile resets the encryption flag, so the encrypted-entry fixture patches the central directory; (3) planning mutants showed the body cap was untested: the multipart epilogue test now covers it. Not yet wired to a route (TASK-04.05). |
| TASK-04.04 | Hashing + dedup | COMPLETED | 04.03 | app/services/documents.py (DocumentService.add: pre-check → store → insert under KB FOR SHARE; DOCUMENT_DUPLICATE with existing_document_id; shielded cleanup), app/repositories/documents.py (find_active_by_hash), app/repositories/knowledge_bases.py (get_active for_share), app/storage/local.py (temp files in `<root>/.tmp/`: Windows path-length fix), docs/development/README.md | tests/integration/test_document_intake.py (14), tests/unit/test_storage.py (+1: real document key in a deep root, both backends) | ruff · mypy · pytest 719 passed, 1 skipped · intake tests green 5× in random order · duplicate → 409 with existing id and **zero** put() calls · same bytes allowed in another KB/org and after the old copy is DELETING · two identical uploads held at a barrier after the pre-check → exactly one document, the loser reports the winner's id, only the winner's object remains · deleted/unknown/foreign KB → 404, nothing stored · KB deleted while storing, DB error after storing, content mismatch, cancellation mid-request → object removed · a KB soft delete is blocked (lock timeout) while an insert holds FOR SHARE · failed cleanup logged by key, filename never logged · mutations, all 9 killed (no pre-check, pre-check counts DELETING, loser keeps object, loser reports itself, no share lock, no KB re-check, no content check, cleanup not shielded, put outside the cleanup scope) | Three real bugs found: (1) LocalStorage temp names pushed real document paths past Windows' 260-character limit (04.01 tests used short keys); (2) reading an id after `session.rollback()` triggers a lazy refresh (MissingGreenlet), ids are now read first; (3) a cancellation landing while put() finishes left an orphan; put() is now inside the cleanup scope. Two tests were added because mutants would otherwise have survived (put spy; lock-ordering test). Processing job + route: TASK-04.05. |
| TASK-04.05 | Upload endpoint (PENDING + QUEUED job) | COMPLETED | 04.02–04.04 | app/api/v1/documents.py (upload_document: raw request stream, multipart OpenAPI body), app/schemas/documents.py (DocumentOut, JobOut, DocumentAccepted), app/services/documents.py (job in the same transaction; require_knowledge_base; read transactions end with commit), app/repositories/documents.py (add_job), app/dependencies/storage.py (StorageDep), app/main.py (app.state.storage, closed on shutdown), app/storage/{base,local,s3}.py (aclose; LocalStorage touches no disk until the first write), app/config/settings.py + app/dependencies/rate_limit.py (upload_per_user_per_hour = 30, fails open), .env.example, API_SPECIFICATION.md, DECISIONS.md (ADR-011 note), docs/architecture/security-model.md, docs/development/README.md | tests/integration/test_documents_upload_api.py (19), tests/integration/tenancy.py (+1 case; cases may send multipart files), tests/integration/test_document_intake.py (updated), tests/integration/conftest.py (storage in each test's tmp dir; upload limit high), tests/unit/test_storage.py (factory touches no disk) | ruff · mypy · pytest 741 passed, 1 skipped · 202 with PENDING document + QUEUED INGEST job, file stored, no storage key in the response · PDF/DOCX/text detected · every role may upload · KB stats show it · duplicate → 409 with id, still 1 document/1 job/1 file · 415/422/413/400 leave no rows and no files · unknown or deleted KB → 404 with **0** body chunks read · job insert failing → neither row, file removed · 30/hour per user (third of a limit of 2 → 429, another user unaffected) · Redis down → upload still 202 · OpenAPI shows the multipart body · isolation: 31 cases pass · no `.data/` written by tests · mutations, all 7 killed (no job, job in a separate transaction, no early KB check, no upload limit, upload limit fails closed, read transaction ended with rollback, declared type trusted) | Found by the isolation framework's positive control: the route's `OrgContext.user` lives in the request session, and the service's `rollback()` (used to end read-only transactions) expired it → MissingGreenlet; read-only transactions now end with `commit()`. Also fixed before it could bite: LocalStorage created folders in `__init__`, which would have crashed app startup in the read-only container now that storage is built at startup. Processing (Celery) starts in Phase 5: jobs wait in QUEUED. |
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

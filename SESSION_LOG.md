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

- **TASK-01.09 COMPLETED**: PR #1 CI green on db60f78 (Backend: 119 passed on Linux incl. integration; Image: healthy, uid 10001, JSON logs). PR #1 merged by fast-forward; main push run 45812054140 SUCCESS. Auto-fix could not be enabled (repo outside the session folder).
- **PHASE-01 COMPLETED (9/9).**

- **TASK-02.01 COMPLETED**: SQLAlchemy 2.1.9 (asyncio), Base + naming convention, UUIDPrimaryKeyMixin (own RFC 9562 UUIDv7), TimestampMixin (server now(), eager_defaults for async-safe reads), create_engine (pre_ping, recycle, application_name), DbSession dependency (rollback on error), engine on app.state + lifespan dispose. 140 tests passed; no leaks after 100 requests (pool and pg_stat_activity).

- **TASK-02.02 COMPLETED**: Alembic 1.20.1 async env (URL from settings or `config.attributes`; JSON logging; selector loop on Windows; compare_type + compare_server_default), typed revision template, UTC date-prefixed file names, ruff post-write hooks, model registry in `app/models/__init__.py`, `dev migrate` / `dev revision`, migrations in the Docker image. Tests, each on a scratch DB dropped WITH FORCE: no URL in ini, single head, round trip, stairway, drift, offline SQL. 146 passed.
- Proof with a temporary probe model (removed afterwards): the drift test failed without a migration; `dev revision` generated a correctly named, formatted file; all tests passed; a deliberately broken `downgrade()` made round trip + stairway fail; `dev migrate` and `migrate -1` worked on the dev DB.
- Slips caught: (1) my first restore of the deliberately broken probe revision used the wrong backup path; detected by checking the file, restored from the real backup, re-tested before cleanup. (2) A long docs-update shell script failed to parse; verified nothing had been applied, then redid the edits with direct file edits.

- **TASK-02.03 COMPLETED**: models User, Organization, OrganizationMembership (+ OrgRole StrEnum with rank/at_least), RefreshToken; relationships lazy="raise" + passive_deletes; first migration 05181d5746d1 (dev DB migrated). New shared fixtures in tests/integration/conftest.py: `database_url` (empty scratch DB) and session-scoped `migrated_database_url` (built by the real migration). 167 tests passed.
- **Bug caught in hand review**: `func.lower("email")` made autogenerate emit an index on `lower('email')`, a constant that allows ONE user in total. Fixed (index built from `User.email`), migration regenerated, and a positive test added. Proven: with the bug reintroduced, `test_different_emails_can_both_register` fails while the duplicate-rejection test still passes. A negative-only test would have missed it.

- **TASK-02.04 COMPLETED**: models KnowledgeBase, Document (+DocumentStatus, chunk_id uuid5), DocumentChunk (no id default by design), ProcessingJob (+JobType/JobStatus/JobStage, ACTIVE_JOB_STATUSES); migration 6228847616cc (dev DB migrated). Shared test infrastructure: `db_engine` / `session_factory` fixtures (truncate every table after each test) and `tests/integration/db_helpers.py`. 198 tests passed.
- **Design strengthened** (recorded in DATABASE_DESIGN §4): chunks and jobs now have composite tenant FKs instead of trusting the worker; chunks also pin their knowledge base, the future vector-search filter.
- Proofs: three concurrent identical uploads → exactly one wins (partial unique index, no app lock). Mutations: a single-column KB FK lets a cross-tenant document in; a non-unique active-job index allows a second active job. Both tests fail as intended; migration restored byte-identical.
- Learned: SQLAlchemy itself warns (an error under our policy) when a chunk is flushed without an id; the test now covers both the ORM and the database layer.

- **TASK-02.05 COMPLETED**: models Conversation (KB tenant FK + membership ownership FK), Message (+MessageRole/MessageStatus), MessageCitation (snapshot, SET NULL links), UsageEvent (+UsageEventType, numeric(12,6) cost, no FKs to document/message); `CreatedAtMixin` for append-only rows; migration ba17b2b568e9 (dev DB migrated). 214 tests passed.
- **Design strengthened**: conversation ownership tied to membership (ADR-020). Consequence: removing a member deletes their private conversations in that org. **Flagged to the user as open to veto** (alternatives: RESTRICT or soft-archive).
- The migration safety net caught a real model/migration mismatch: `Float(precision=24)` in the model versus `REAL` reflected from the DB. Fixed in both.
- My test's float demonstration was wrong: Python 3.12's `sum()` uses compensated summation, so 10×0.1 == 1.0. It now demonstrates with `0.1 + 0.2 != 0.3`; the database assertion (exact Decimal 1.000123) was correct all along.
- Mutation proofs: a plain user FK lets a non-member own a conversation; CASCADE citations disappear with their document. Both tests fail as intended; migration restored byte-identical.
- Tooling note: a PowerShell `Remove-Item Env:...` cleanup was blocked by a safety hook (misread as a path); env vars are now set inline via `cmd /c "set X=...&& ..."`.

- TASK-02.05 CI on main: run 45819950502 SUCCESS (214 passed).
- **TASK-02.06 COMPLETED**: `app/core/errors.py` (AppError + Authentication, PermissionDenied, NotFound, Conflict, PayloadTooLarge, UnsupportedMediaType, ValidationFailed, RateLimited, UpstreamUnavailable; no web-framework imports), `app/api/errors.py` (STATUS_BY_ERROR, envelope builder, handlers for AppError / HTTPException / RequestValidationError), `app/schemas/errors.py`. RequestContextMiddleware now answers unexpected exceptions with the generic 500 itself (no re-raise unless the response already started), which removes the duplicate uvicorn traceback from TASK-01.07. 241 tests passed.
- Security fixes over FastAPI defaults: the 422 no longer echoes submitted input (password test); the 500 never includes exception text.
- Verified on a real uvicorn (throwaway factory in the scratchpad, not committed): 1 structured traceback with request_id, 0 uvicorn duplicates, secret redacted.
- My test slip: a leak check for the word "crash" matched my own request id "req-crash"; renamed the id, kept the check strict.

- TASK-02.06 CI on main: run 45821092406 SUCCESS (241 passed).
- **TASK-02.07 COMPLETED**: signed `CursorCodec` (HMAC-SHA256 truncated to 128 bits, key derived from the JWT secret, scope-bound), `KeysetOrder` / `apply_keyset` / `fetch_page` (row-value comparison, LIMIT n+1, no COUNT), `Page[T]` schema, `PageParams` / `CursorCodecDep` dependencies, new `BadRequestError` (400) with `INVALID_CURSOR`. Adopted PEP 695 generic syntax (ruff UP046/UP047). 276 tests passed.
- Decision: cursor errors are 400 (the spec's "malformed"), `limit` errors 422; every cursor failure gives the same response (reveals nothing about why).
- Query plan checked in a rolled-back transaction with 20k rows: page 1 and a ~9,000-deep page each read 22 index rows. Follow-up recorded on TASK-14.06: incremental sort on id for timestamp ties.
- My test slip: set pydantic `from_attributes` after class creation (no effect); now declared in the class.

- TASK-02.07 CI on main: run 45822653574 SUCCESS (276 passed).
- **TASK-02.08 COMPLETED → PHASE-02 COMPLETED (8/8).** pytest-randomly 5.1.0 (always on; seed printed), integration auto-marker by folder, `factories.py`, `test_settings` / `api_app` / `api_client` fixtures on the migrated scratch DB, `test_harness.py`, docs/testing/README.md. 281 tests passed.
- Isolation measured instead of assumed: TRUNCATE 60 ms vs DELETE 29 ms (12 tables), but the larger cost was NullPool opening a new connection (33 ms) for every session. A per-test pool plus one-round-trip DELETE took the integration suite from ~43 s to ~28 s. Savepoint rollback rejected: the concurrency tests need separate committed transactions.
- Verified: random order is real (same seed = same order, different seeds differ, 3 shuffled full runs green); unit tests pass with PostgreSQL stopped.
- Slip: my first seed-capture script used `-q`, which hides the seed header; re-checked ordering explicitly with `--collect-only` per seed.

- TASK-02.08 CI on main: run 45824482568 SUCCESS (281 passed, random seed 2021584766). PHASE-02 done.
- **TASK-03.01 COMPLETED**: `app/core/passwords.py` (PasswordHasher: Argon2id m=19 MiB t=2 p=1, verify_and_update → rehash on login, NFKC, dummy_verify, *_async via worker thread; password_policy_violations / validate_password → 422 PASSWORD_POLICY with field errors). ADR-021. Measured ≈53 ms per hash (library default ≈116 ms). 308 tests passed.
- Library default checked before use: pwdlib's Argon2 defaults to 64 MiB/t=3/p=4, so parameters are explicit.
- Pitfall found and fixed: `\uXXXX` escapes I write are decoded into literal characters by the editing tools, which put invisible combining accents and full-width look-alikes into test source (ruff RUF001 flagged them; mypy crashed once on that file). Look-alikes are now real `\u` escapes, written via a script that builds the backslash with chr(92). Added a guard that the Unicode test inputs truly differ, so the test can't pass trivially.
- My bug: `await` inside a generator expression made an async generator; caught by mypy and the test.

- TASK-03.01 CI on main: run 45826910200 SUCCESS (308 passed, seed 3523280490).
- **TASK-03.02 COMPLETED**: `app/core/tokens.py` (AccessTokenService: HS256 pinned, required claims sub/iat/exp/jti/typ/iss/aud, typ must be "access", key derived from the JWT secret, 30 s leeway, injectable clock; TokenExpiredError / InvalidTokenError → 401), `app.state.access_tokens`. pyjwt 2.15.1. 338 tests passed.
- Attack-shaped tests: tampering, foreign secret, raw secret, alg=none, correctly signed HS512, and validly signed tokens with one wrong claim each (built with the private key on purpose, plus a guard test that the base payload is valid). Mutation: disabling the typ check lets a refresh-typed token in; test fails as intended.
- My test bugs: hand-built JWT segment serialised datetimes (fixed: epoch ints); PyJWT's HS512 short-key warning is expected in the forgery test (suppressed there only).

- TASK-03.02 CI on main: run 45828110749 SUCCESS (338 passed, seed 1709187362).

## Session 2 — 2026-11-27

**Phase:** PHASE-03 (Auth & multi-tenancy).

- Repo check at start: working tree clean; "changed on disk" notices were stale (branch switches), nothing pending from the user.
- **TASK-03.03 COMPLETED**: refresh-token primitives (`app/core/refresh_tokens.py`), cookie helper (`app/api/auth_cookies.py`), `RefreshTokenRepository` (FOR UPDATE lookup, family revocation), `SessionService` (start / rotate / end / end_all_for_user). ADR-022: strict reuse detection, no grace period (SPA must single-flight refreshes). 357 tests passed; refresh tests green under 3 random seeds.
- Proofs: reuse revokes the newest token (checked from a fresh session); concurrent double use → exactly one success, then the family is revoked; other devices unaffected. Mutations: no reuse detection → 5 tests fail; removing the commit before the reuse error → 2 fail (the revocation would silently roll back).
- My test bug: the reuse-log test depended on another test having configured JSON logging (random order would make it flaky); it now configures logging itself.

- TASK-03.03 CI on main: run 45981051814 SUCCESS (357 passed, seed 154740891).
- **TASK-03.04 COMPLETED**: auth router (register, login, refresh, logout, me, update me, change password), `AuthService`, `CurrentUser` (Bearer via HTTPBearer, so Swagger shows "Authorize"; re-checks the user is active on every request), user/organization repositories, email-validator 2.4.0 (EmailStr without DNS lookups). API_SPECIFICATION auth section updated with the final error codes. 391 tests passed.
- Proofs: 3 concurrent identical registrations → 201/409/409 with nothing left behind; mutations: without the dummy verify an unknown email answered in 12.8 ms vs 56.8 ms (test fails); without the is_active check a deactivated account logged in (test fails). Real uvicorn + curl cookie jar: HttpOnly cookie, path /api/v1/auth, sent to /auth/me but not /health/live; test account removed from the dev DB afterwards.
- Fixed from the manual run: slugs like "manual-check-s-workspace" (apostrophe became a hyphen); apostrophes are now dropped; slugify unit tests added.
- Slips: (1) a no-echo check used the password "short", which appears in the error type "password_too_short" (now a distinctive value); (2) PowerShell 5.1 stripped quotes from inline JSON passed to curl.exe (now sent from a file); (3) the edit tool again decoded a ’ escape into the literal character (rewritten via chr(92)).
- Known limitation (recorded): a failed refresh does not clear the cookie, because error responses are built by the exception handler; the client then goes to login, which overwrites it.

- TASK-03.04 CI on main: run 45983104540 SUCCESS (391 passed, seed 1293718171).
- **TASK-03.05 COMPLETED**: `app/dependencies/orgs.py` (OrgContext, OrgContextDep, require_role, AdminContext, OwnerContext; 404 ORGANIZATION_NOT_FOUND for non-members and unknown orgs alike), `OrganizationRepository.get_membership`, user_id/organization_id bound to request logs and removed by the middleware afterwards. 410 tests passed; org tests green under 3 seeds.
- Key insight from mutation testing: a membership lookup that drops the organization filter passed the whole role matrix and failed only the cross-tenant tests. TASK-03.08 must make cross-tenant cases mandatory for every org-scoped route.
- Slip (recurring): section-divider comments over 100 chars; keep them short from now on.

- TASK-03.05 CI on main: run 45984278491 SUCCESS (410 passed, seed 3735068912).
- **TASK-03.06 COMPLETED**: organizations router + OrganizationService (create/list/get/rename; list/add/re-role/remove members), OrgContext moved to `app/core/tenancy.py` (broke a services↔dependencies import cycle), org creation shared with registration. Per-organization row lock + re-read of actor/target roles for every membership change. ADR-020 cascade verified by test. 446 tests passed.
- **Important lesson**: the first race tests (asyncio.gather) and the first stale-role test PASSED with the lock removed / with the stale role trusted. Requests did not overlap at the critical point, and the stale role was refreshed by the org check. Rewrote them with deterministic hooks (Barrier after count_owners; demotion injected inside lock). Now both mutations fail 3/3 runs. Documented in docs/testing/README.md: prove every concurrency test with a mutation.
- My bugs: (1) `(await ... for ...)` async generator again, in a test helper (mypy flagged it); (2) long section dividers again.
- Plan change vs session-1 note: lock the organization row (one mutex per org) instead of the owners' membership rows; simpler and covers the actor-role re-check.

- **Decision (project owner, 2026-11-27): ADR-020 confirmed final**: removing a member keeps deleting their private conversations in that organization. No code change; DECISIONS.md, tracker and docs updated.

- **TASK-03.07 COMPLETED**: `app/core/rate_limit.py` (sliding-window log in one Lua script: Redis `TIME` clock, rejections not recorded, keys hold sha256 of the identity), `app/dependencies/rate_limit.py` (`RateLimitGuard` on app.state; fails closed with 503 `RATE_LIMIT_UNAVAILABLE`), `RateLimitSettings` (`CORTEX_RATE_LIMIT__*`; deployed envs require it enabled), Redis client on app.state (0.5 s timeouts, closed on shutdown). Register 5/h/IP; login 10/min/IP and 5/min/account (email lower-cased and trimmed). Test settings give each test its own key prefix and very high limits. 472 tests passed.
- Proofs: 30 concurrent hits against a limit of 5 → exactly 5 allowed; a non-atomic check-then-add mutant fails that test 3/3. Seven more mutants killed (off-by-one, no normalization, fail-open default, limits removed, raw identity in key, recording rejections).
- Lesson: the "rejections are not recorded" test first passed against a mutant that recorded them, because the key's TTL (refreshed only on allowed requests) expired the evidence. The rewritten test keeps the key alive with a later allowed request.
- Slips: `model_copy` does not validate, so a test passed a plain str where a SecretStr was expected; `python` is not on PATH in Git Bash (use `uv run python`).

- TASK-03.07 CI on main: run 45990382917 SUCCESS (472 passed, seed 3419635856).
- **TASK-03.08 COMPLETED**: `tests/integration/tenancy.py` (two-tenant `World`, `ISOLATION_CASES`, `NOT_TENANT_SCOPED` with reasons, whole-database `snapshot`) and `tests/integration/test_tenant_isolation.py`. Every API operation must be classified; each org-scoped route is probed by B's owner, admin and member with A's org id (404 identical to an unknown org, database unchanged), by B's owner with B's org id and A's resource ids (404 identical to an unknown id), and positively by A's owner. 489 tests passed.
- Proofs: six mutants, all killed by the intended test (membership ignoring org, member target in any org, side effect before the 404, differently worded foreign 404, deleted registry case, new unregistered route).
- Surprise: FastAPI 0.165 no longer flattens included routers into `app.routes` (lazy private `_IncludedRouter` nodes), so my first registry test found zero routes and called every entry stale. Routes now come from the OpenAPI schema, a test forbids `include_in_schema=False` under app/, and the registry test asserts it found routes at all.
- Slip: a sync test used the async `api_app` fixture (pytest refuses); made it async.

- TASK-03.08 CI on main: run 45991457906 SUCCESS (489 passed, seed 4270192420).
- **TASK-03.09 COMPLETED → PHASE-03 COMPLETED (9/9)**: `docs/architecture/security-model.md`. Each control links its code; every constant, test name, file link and planned-task owner was checked against the repository. ARCHITECTURE.md §6–7 brought up to date (isolation suite now 404-only; JWT claims include iss/aud).
- Found while verifying: (1) every refresh rotation issues a fresh 14-day expiry, so an active session never ends on its own (G-2, accepted, revisit in TASK-15.01); (2) `cors_origins` is validated but no CORS middleware is wired (G-6; safe, browsers refuse cross-origin calls, until TASK-15.03).
- Slip caught before commit: the first draft cited TASK-11.05 as the owner of a general per-user limit; that task only covers the ask endpoint.

- TASK-03.09 CI on main: run 45992460921 SUCCESS (489 passed, seed 1598040626). **PHASE-03 complete and green.**
- **TASK-04.01 COMPLETED** (Phase 4 started): `app/storage/` with the `StorageBackend` protocol, `LocalStorage`, `S3Storage` and `create_storage`. Dependencies added (approved in TECH_STACK): boto3 1.44.12; dev: moto 5.2.6 with its server, types-boto3[s3]. 562 passed, 1 skipped.
- S3 tests use moto's **server** over real HTTP (in-process, no Docker), so presigned URLs are actually fetched: forced `attachment`, tampered URL → 403.
- Bug found by the tests: when a read stopped early (over its limit) or an upload failed mid-stream, the async generator holding the file/HTTP response was abandoned, not closed; on Windows the open file surfaced as a ResourceWarning error. Fixed with `aclosing` in `read_all` and around upload streams (`as_chunks` also closes its source); the protocol now returns `AsyncGenerator` so callers can close it.
- Test design: the local backend's second wall (resolved path inside root) is tested with key validation switched off, because the symlink test cannot run on this Windows machine (no Developer Mode); it runs on Linux CI. `C:/x` escapes only on Windows, so it is tested at the key-validation wall, not the cross-platform second-wall test.
- 12 mutants, all killed. The outage test uses a one-attempt client (Windows needs ~2 s to refuse each connection; with production retries it took 33 s).
- Decision: storage is not put on `app.state` until the upload endpoint needs it (04.03–04.05).

- TASK-04.01 CI on main: run 45997758838 SUCCESS (563 passed, **0 skipped**: the symlink-escape test ran on Linux and passed).
- **TASK-04.02 COMPLETED**: knowledge bases API (`app/api/v1/knowledge_bases.py`, service, repository, schemas). Embedding profile taken from settings (`EmbeddingSettings.profile`) and fixed per KB. Stats for a page come from one grouped query. Soft delete is one conditional UPDATE (concurrent deletes → 202 + 404). 616 passed, 1 skipped.
- **The isolation framework paid off twice**: (1) its positive controls caught three routes answering 500 to the legitimate owner (`Result.tuples()` is deprecated in SQLAlchemy 2.1 and warnings are errors); (2) the mutation "read ignores the organization" was caught *only* by its foreign-resource probes.
- Test refactor: `Api`, `team`, `code` moved to `tests/integration/api_helpers.py`; the `api` fixture now lives in `tests/integration/conftest.py` (importing a fixture by name clashed with test parameters, ruff F811).
- 10 mutants, all killed. With the overlap check removed, the database CHECK still refused the row (tests fail on the resulting 500 instead of a 422): defence in depth works, and the API check gives the better error.
- Slips: mypy flagged `**dict[str, int]` possibly filling the `who` parameter (made keyword-only, widened the test type).

- TASK-04.02 CI on main: run 46001596528 SUCCESS (617 passed, seed 3012620986).
- **TASK-04.03 COMPLETED**: `app/uploads/` (receive, detect, filenames). Dependency added: python-multipart 0.0.34 (FastAPI's own multipart library, now used directly; TECH_STACK updated). ADR-023 records why uploads bypass `UploadFile` (it buffers the whole body before the route runs). 704 passed, 1 skipped.
- Proofs that limits act on arrival: a declared over-limit Content-Length is refused with 0 body chunks read; a chunked 64 MiB body is refused after at most 18 of 1024 chunks; a lying Content-Length and an endless multipart epilogue are capped the same way.
- Found while testing: tabs/newlines in filenames were deleted rather than turned into spaces (fixed); `zipfile` silently resets the encryption flag when writing (the fixture now patches the central directory); planning mutants showed the overall body cap had no test (the epilogue test added). 13 mutants, all killed.
- Choices recorded in ADR-023: no libmagic; text must be UTF-8 (Latin-1 refused with reason `not_utf8_text`); a `%PDF-` polyglot is a PDF (always downloaded as an attachment); ZIP limits use declared sizes, so the Phase 6 DOCX parser must cap real decompressed bytes.

- TASK-04.03 CI on main: run 46003011982 SUCCESS (705 passed, seed 3098399817).
- **TASK-04.04 COMPLETED**: `DocumentService.add` (pre-check → store → insert under `FOR SHARE`), `DocumentRepository.find_active_by_hash`, `get_active(for_share=True)`. Scope note: the processing job and the route are 04.05; this task built the service they will call. 719 passed, 1 skipped.
- **Three real bugs, all found by the new integration tests:**
  1. `LocalStorage` put its temp file next to the destination (`.original.<uuid>.tmp`); with real ~130-character keys in a deep root the path exceeded Windows' 260-character limit. Temp files now go to `<root>/.tmp/<hex>.tmp`; regression test with a real key on both backends. (04.01's tests used short keys, so they could not see it.)
  2. `session.rollback()` expires every loaded object even with expire_on_commit off; reading `existing.id` afterwards triggered a lazy refresh → `MissingGreenlet`. Ids are now captured before the rollback.
  3. A cancellation landing while `put()` was finishing (anyio lets the worker thread complete, then raises) left an orphan object. `put()` now runs inside the shielded cleanup scope.
- Lesson (again): two mutants would have survived on outcome-only tests: skipping the pre-check (the unique index still answers 409 and the object is cleaned up) and dropping the `FOR SHARE` lock (same end state). Added a `put()` spy and a lock-ordering test (a soft delete with `lock_timeout` must be blocked during the insert). 9 mutants, all killed.

- TASK-04.04 CI on main: run 46005254821 SUCCESS (720 passed, seed 885848928).
- **TASK-04.05 COMPLETED**: upload endpoint (`app/api/v1/documents.py`) reading the raw request stream; document + `INGEST`/`QUEUED` job in one transaction; storage on `app.state.storage` (closed on shutdown); upload quota 30/hour per user (fails open). 741 passed, 1 skipped.
- **Bug found by the isolation framework's positive control (third time it has paid off):** the route's `OrgContext.user` is loaded in the request's session; `DocumentService` ended its read-only transactions with `rollback()`, which expired that User, so `ctx.user_id` later needed a lazy refresh → MissingGreenlet → 500 for the legitimate owner. 04.04's tests built the context in a separate session and could not see it. Rule now in the dev guide: end read-only transactions with `commit()`; `rollback()` only after a failure, and read what you need first.
- Fixed pre-emptively: `LocalStorage.__init__` created folders; with storage now built at app startup, the read-only container would have failed to start. Folders are now created on the first write (test: the factory touches no disk).
- Order of checks in the route is deliberate (membership → quota → KB exists → stream body → detect → dedup/store/insert): an unknown KB is refused with 0 body chunks read.
- Tests now run uploads in each test's tmp dir (`test_settings.storage.local_root`); verified no `.data/` appears in the repository. 7 mutants, all killed.

- TASK-04.05 CI on main: run 46006972621 SUCCESS (741 passed) **but 1 skipped on Linux**: the symlink-escape test. Cause: my 04.05 change made LocalStorage create its root lazily, so the test's `symlink_to` inside the not-yet-existing root raised FileNotFoundError, which its broad `except OSError` turned into "symlinks unavailable". Fixed: the test creates the root first, and only Windows may skip (elsewhere the error is raised). Lesson: a skip in CI is a failure to investigate, not a pass; compare the skip count with the previous run.

- Symlink-test fix on main: run 46007340808 SUCCESS (742 passed, **0 skipped**).
- **TASK-04.06 COMPLETED → PHASE-04 COMPLETED (6/6)**: document list/get/status/download. Correction to my previous note: delete and reprocess are TASK-05.05 (they need the cleanup worker), as PROJECT_PLAN says. 775 passed, 1 skipped (Windows symlink only).
- Design: "visible" = not DELETING, live KB, caller's org (one join). Search escapes LIKE wildcards; the cursor scope contains a hash of `q`, not the text (cursors are readable). Download reads the first chunk before the response starts, so a missing file is a clean 500 instead of a truncated 200.
- Test slips (mine, in expectations, not code): newest-first order in a search expectation; a filename containing a backslash, which sanitization rightly turns into a path separator. Also again a heredoc escape that put a real newline into an f-string; fixed with the editor (use the Edit tool for code containing escapes).
- 12 mutants, all killed; "lookup ignores the organization" is again caught only by the isolation framework.
- **Before Phase 5:** Docker Desktop still has ~2.8 GB RAM (B-02); worker + beat + Postgres + Redis + Qdrant need more. Raise to 6–8 GB (Settings → Resources) before TASK-05.06's compose services.

- TASK-04.06 CI on main: run 46009619814 SUCCESS (776 passed, 0 skipped). **PHASE-04 complete and green.**
- **TASK-05.01 COMPLETED** (Phase 5 started): `app/workers/celery_app.py`, `runtime.py`, `tasks.py`; `WorkerSettings`. Dependencies: celery[redis] 5.7.0 (approved in TECH_STACK); dev: celery-types (stubs). 789 passed, 1 skipped.
- **ADR-004 amended**: `asyncio.run()` per task would break the async pool from the second task on (connections belong to their loop). One `asyncio.Runner` per process instead; the mutation "asyncio.run per task" makes the reuse test fail, so the problem is proven, not theoretical.
- Per-process context is pid-checked (a forked child never uses its parent's pool; a child's shutdown never closes the parent's).
- Container check (acceptance "worker boots and logs JSON"): ran the rebuilt image with `celery … worker` on the compose network with the API's hardening; it consumed a ping sent from the host via Redis (db 1), created its context in the child process, logged JSON with task ids, and shut down warmly, disposing the pool. Test container removed.
- One mutant survived at first (task_id not unbound after a task); added a leak test. 8 mutants, all killed.
- Reminder: Docker RAM still ~2.8 GB (B-02); needed by TASK-05.06.

- TASK-05.01 CI on main: run 46011465487 SUCCESS (790 passed, 0 skipped).
- **TASK-05.02 COMPLETED**: `JobRepository` (every transition is one conditional UPDATE … RETURNING) and `JobService` (claim, heartbeat, succeed, fail, cancel). Fencing token = the `attempts` value returned by the claim (no schema change). Lease from the database clock. Document status moves with INGEST jobs in the same transaction, never over DELETING. 813 passed, 1 skipped.
- Proof of the claim's atomicity: 32 concurrent claimers over 8 jobs; a read-then-write mutant fails 5/5 runs **without** any artificial delay (I first ran it with a 50 ms pause in the mutant, then checked my sed had not removed it and reran properly without).
- Slips: a draft block left in `fail()` (caught on re-read before testing); a meaningless test (empty-table check) removed; forgot to re-run the formatter after a test edit (the check caught it).

- TASK-05.02 CI on main: run 46013589362 SUCCESS (814 passed, 0 skipped).
- **Project owner (2026-11-28): "don't trouble the storage or RAM."** Standing rule from now on: targeted test runs while iterating, one full check per task, mutation runs on the relevant test files only, no Docker builds or extra containers unless a task needs them (and remove any I start), small temp files, never delete user data unasked. With the owner's approval, pruned unused Docker build cache: 8.66 GB → 3.91 GB (4.75 GB freed); verified all 6 running containers (Cortex, yenta, analytics-prototype), 13 images and 8 volumes unchanged and Cortex DB tests still passing. Also: raising Docker Desktop's RAM (B-02) would take *more* of the PC's memory, so the remaining Phase 5 work is designed to be testable without permanent worker/beat containers.
- **TASK-05.03 COMPLETED**: `app/workers/base.py`: error types, `run_job`, `JobRun.heartbeat`, full-jitter backoff, `execute_job_task`. 833 passed, 1 skipped.
- Mutation lesson: deleting the "lost job" branch did not corrupt data (05.02's fencing refused the write) but would have logged a normal lease loss as an unexpected error with a traceback (false alarms). Added a log assertion; 9 mutants, all killed.

**Exact next task:** **TASK-05.04**: (1) **post-commit enqueue**: after `DocumentService.add` commits the document + job, enqueue `cortex.ingest_document(job_id)`; if the broker is down the upload still succeeds (job stays QUEUED, logged), never enqueue before the commit. The ingest task body is a placeholder until Phase 6 (it can claim and `PermanentError("NOT_IMPLEMENTED")`? better: succeed with no chunks only once Phase 6 exists, so for now it must not run real work; decide and document). (2) **beat reconciler** task `cortex.reconcile_jobs` (every minute, settings): re-enqueue QUEUED older than N minutes and RETRYING idle longer than the max backoff; RUNNING with an expired lease → RETRYING (fenced UPDATE: `status = RUNNING AND lease_expires_at < now()`), or FAILED (`LEASE_EXPIRED`) when attempts are used up, document FAILED too. Idempotent; tests simulate a lost enqueue and a crashed worker; run reconcile twice → same result. Keep it light: no beat container needed for tests (call the task function directly).

**How to resume:** say "Resume Cortex development". The assistant reads PROJECT_PLAN, TASK_TRACKER, this log and DECISIONS, checks the repo state, then continues from the next task.

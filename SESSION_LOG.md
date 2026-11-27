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

**Exact next task:** **TASK-03.08**: tenant-isolation test framework. Enumerate every org-scoped route from the app's router (anything under `/orgs/{org_id}`), and make a test fail if a route has no registered cross-tenant case. For each route: a member of org B calling with org A's id (and A's resource ids where present) gets 404 with a body identical to an unknown id, and nothing in A changes. Reusable fixtures: two orgs with owners/admins/members. Document how to add a case when adding a route.

**How to resume:** say "Resume Cortex development". The assistant reads PROJECT_PLAN, TASK_TRACKER, this log and DECISIONS, checks the repo state, then continues from the next task.

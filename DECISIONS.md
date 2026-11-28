# Cortex — Architecture Decision Records

Format: short ADRs (context → decision → consequences). All ADRs below were accepted with the plan on 2026-11-26; open questions still apply their recommended default until you answer. Superseded decisions stay in the file with a pointer to the replacement. Longer write-ups, when needed, go in `docs/decisions/ADR-NNN-*.md`.

## Pending your input (before/at plan approval)

| # | Question | My recommendation |
|---|---|---|
| P-1 | Which LLM endpoint will you use for development? | Any OpenAI-compatible API you already have a key for, with a cheap/fast model as default. Local Ollama is fine if you'd rather spend nothing (slower, lower quality). |
| P-2 | Default embedding model: local FastEmbed or a paid API? | **Local `BAAI/bge-small-en-v1.5`** (free, offline, CI-friendly). Paid embeddings stay a config option. |
| P-3 | May MEMBERs upload documents, or ADMIN+ only? | MEMBERs may upload; only uploader or ADMIN may delete (ADR-010). |
| P-4 | Will this live on GitHub? | **Answered 2026-11-26: yes, on your personal GitHub account** (not the work account). Commits use a repo-local personal identity. |
| P-5 | Can you raise Docker Desktop memory to 6–8 GB? | Yes, before Phase 5. |

---

### ADR-001 — Modular monolith + separate worker processes
* **Status**: Accepted (2026-11-26)
* **Context**: One developer, one data model, strong consistency needs between documents/chunks/jobs.
* **Decision**: Single backend codebase deployed as `api`, `worker`, `beat` processes. Module boundaries enforced by package layout and layering rules, not network boundaries.
* **Consequences**: Simple deploys, shared models, easy refactoring. Worker can scale independently. A module could be extracted later if a real need appears.

### ADR-002 — PostgreSQL is the source of truth; Qdrant is a derived index
* **Status**: Accepted (2026-11-26)
* **Context**: There are no distributed transactions across PG and Qdrant.
* **Decision**: All chunk text and metadata live in PG. Qdrant holds vectors + minimal filter payload (ids, org, kb, document, chunk_index, page). Writes go PG first; Qdrant is rebuilt/repaired from PG by idempotent tasks and a reconciler.
* **Consequences**: Retrieval needs a PG hydration step (one PK lookup query, cheap). Qdrant can be fully rebuilt from PG at any time.

### ADR-003 — `uv` for Python environment and dependency management
* **Status**: Accepted (2026-11-26)
* **Context**: No system Python on PATH; uv 0.13 with CPython 3.12.16 already installed. Need reproducible installs locally, in Docker and CI.
* **Decision**: `uv` + `pyproject.toml` + committed `uv.lock`; Python pinned to 3.12.
* **Consequences**: Fast, reproducible installs; one tool for venvs and Python versions. Contributors need uv (single binary).

### ADR-004 — Async SQLAlchemy with psycopg 3; workers reuse async services via `asyncio.run`
* **Status**: Accepted (2026-11-26)
* **Context**: FastAPI benefits from async I/O (LLM calls, DB). Celery tasks are synchronous. Maintaining sync + async repositories would duplicate code.
* **Decision**: Async repositories/services only. Celery tasks are thin sync wrappers calling `asyncio.run(...)`. Each worker process creates its own engine after fork.
* **Consequences**: One code path. Small per-task event loop overhead (negligible versus parsing/embedding). Must never share engines across forked processes.
* **Amendment (2026-11-27, TASK-05.01)**: not `asyncio.run()` per task. It creates a new event loop each time, and an async pool's connections belong to the loop that opened them, so from the second task on pooled connections are unusable (proven: with `asyncio.run` the second task fails). Instead each worker process keeps one `asyncio.Runner` for its lifetime, and the engine lives on that loop (`app/workers/runtime.py`). The context is created lazily in the process that uses it and rebuilt when the pid changes, so a forked child never reuses its parent's pool; it is disposed on `worker_process_shutdown`.

### ADR-005 — Org scope in the URL path, verified server-side on every request
* **Status**: Accepted (2026-11-26)
* **Context**: Requirement: never trust org ids from the frontend, but users can belong to several orgs.
* **Decision**: Org-scoped routes are `/orgs/{org_id}/…`. The path id is a claim; the `OrgContext` dependency verifies membership and role. Non-members get 404. All repositories require `organization_id`.
* **Alternatives**: org id in JWT (forces re-login on org switch, stale roles); `X-Org-Id` header (equivalent, less visible/RESTful).
* **Consequences**: Explicit, cache-friendly, easy to test with an isolation route registry.

### ADR-006 — Short JWT access tokens + rotating opaque refresh tokens in HttpOnly cookie
* **Status**: Accepted (2026-11-26)
* **Context**: SPA must stay logged in without storing long-lived secrets in JS-readable storage.
* **Decision**: 15-min HS256 access JWT kept in memory; 14-day opaque refresh token, SHA-256 hashed in DB, rotated on use, reuse ⇒ family revoked; cookie `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`, plus custom header requirement on `/auth/refresh` against CSRF.
* **Consequences**: XSS cannot steal the refresh token; logout is real (server-side revoke). Access tokens remain valid until expiry (≤15 min) after logout — accepted and documented. Swagger UI testing uses the login response's access token.

### ADR-007 — One Qdrant collection per embedding profile, tenant via indexed payload
* **Status**: Accepted (2026-11-26)
* **Context**: Collection-per-tenant scales poorly in Qdrant; mixing embedding models in one vector space is invalid.
* **Decision**: Collection per *embedding profile*, shared by all orgs; `organization_id` payload index with `is_tenant=true`; `knowledge_base_id`, `document_id` keyword indexes; named vectors `dense` and `sparse` (IDF modifier).
* **Consequences**: Efficient multi-tenant storage; tenant filter is mandatory and enforced in the wrapper (searches without it raise).

### ADR-008 — Embedding versioning through "embedding profiles"
* **Status**: Accepted (2026-11-26)
* **Decision**: Profile = provider + model + dimensions + version → collection name. KB is pinned to a profile; chunks/documents record the profile they were built with. Model change = reindex into a new collection, flip KB when complete.
* **Consequences**: No mixed vectors; controlled reindexing; temporary double storage during migration.

### ADR-009 — pypdf for PDF extraction (not PyMuPDF)
* **Status**: Accepted (2026-11-26)
* **Context**: PyMuPDF is faster/better on layout but AGPL-licensed; pypdf is BSD and pure Python.
* **Decision**: pypdf for MVP behind the parser protocol; PyMuPDF/pdfplumber can be swapped in later if quality measurements justify it and licensing is acceptable.
* **Consequences**: Somewhat weaker multi-column/table handling; mitigated by cleaner and measured via eval set.
* **Note (2026-11-28, TASK-06.01)**: installed as `pypdf[crypto]`. Many PDFs are AES-encrypted with an *empty* user password (restrictions only); decrypting them needs `cryptography`, which was otherwise only a dev dependency. Text uses pypdf's default ("plain") mode, which keeps multi-column text column by column; "layout" mode interleaves columns line by line.
* **Note (2026-11-28, TASK-06.02)**: DOCX is parsed with the standard library (`zipfile` + `XMLPullParser`), not python-docx. python-docx loads the whole package, images included, into memory; streaming reads only `word/document.xml` and `word/styles.xml` through a byte-counting reader and discards each finished element (measured: 5.8 MiB peak for 50,000 paragraphs, 22.6 MiB without discarding), refuses any XML `DOCTYPE`, and needs no extra dependency.

### ADR-010 — Upload and delete permissions
* **Status**: Accepted (2026-11-26) (see P-3)
* **Decision**: MEMBER+ can upload and reprocess; delete allowed to the uploader or ADMIN+; KB management ADMIN+.

### ADR-011 — Redis sliding-window rate limiting implemented in-house
* **Status**: Accepted (2026-11-26)
* **Context**: Popular libraries add little over ~40 lines of Redis logic and some are poorly maintained.
* **Decision**: Small limiter (sorted-set or fixed-window-with-weight) used as a FastAPI dependency with per-route policies. Fail-closed on auth routes, fail-open elsewhere when Redis is down.
* **Consequences**: Fully understood and tested code; one less dependency.
* **Implementation (TASK-03.07, 2026-11-27)**: sliding-window *log* (one sorted set per key) in a single Lua script, so check-and-record is atomic (30 concurrent requests against a limit of 5 → exactly 5 pass; a non-atomic variant fails the test). Clock = Redis `TIME` (shared across API processes). Rejections are not recorded. Keys hold `sha256(identity)`, never a raw IP or email. Auth policies fail closed; the upload quota (TASK-04.05) fails open, because it limits cost rather than protecting credentials.

### ADR-012 — Local ONNX models (FastEmbed) for dense, sparse and reranking by default
* **Status**: Accepted (2026-11-26)
* **Context**: PyTorch-based sentence-transformers add multi-GB images; tests must run offline and free.
* **Decision**: FastEmbed (ONNX Runtime) for `bge-small-en-v1.5` dense, `Qdrant/bm25` sparse, `ms-marco-MiniLM-L-6-v2` cross-encoder. Model files cached in a volume, downloaded at image build or first start.
* **Consequences**: Small images, CPU-only, deterministic tests. Quality ceiling lower than large API models — measured by the eval harness, swappable via config.

### ADR-013 — Hybrid fusion in application code (weighted RRF)
* **Status**: Accepted (2026-11-26)
* **Decision**: Run dense and sparse queries (Qdrant prefetch or two calls) and fuse in Python with weighted RRF so weights are configurable and the algorithm is visible and unit-tested. Qdrant's built-in fusion is benchmarked as a comparison.
* **Consequences**: Slightly more data transferred; full control and explainability.

### ADR-014 — Keyset pagination
* **Status**: Accepted (2026-11-26)
* **Decision**: Opaque cursor over `(sort_key, id)`; no total counts on large lists.
* **Consequences**: Stable under concurrent inserts and O(log n) per page; UI shows "Load more" rather than numbered pages.

### ADR-015 — Celery workers run only in Linux containers
* **Status**: Accepted (2026-11-26)
* **Context**: Celery does not officially support Windows.
* **Decision**: API may run on the host for fast iteration, but worker and beat always run in Docker.

### ADR-016 — Configuration: nested `CORTEX_*` variables, fail-fast validation
* **Status**: Accepted (2026-11-26, TASK-01.03)
* **Context**: Misconfiguration is a top cause of production incidents and secret leaks; typos in env var names are silently ignored by most settings libraries.
* **Decision**: One `Settings` (pydantic-settings) with nested groups addressed as `CORTEX_<GROUP>__<FIELD>`. Secrets are `SecretStr`. Unknown `CORTEX_*` variables (env or `.env`) are rejected. In `staging`/`production` startup fails unless: non-default JWT secret ≥ 32 chars, non-default DB password, `debug=false`, explicit `https://` CORS origins, S3 storage, secure refresh cookie. `.env.example` is kept in sync by a test.
* **Consequences**: A bad deployment fails at boot with a list of every problem instead of running insecurely. Adding a setting requires documenting it in `.env.example` (CI enforces).

### ADR-017 — Dedicated local host-port block for Cortex
* **Status**: Accepted (2026-11-26, TASK-01.04)
* **Context**: The development machine runs other projects whose containers bind the default ports (a work Redis on 6379, Qdrant on 6333, several Postgres instances). Stopping them is not acceptable.
* **Decision**: Cortex binds host ports 15432 (Postgres), 16379 (Redis), 16333/16334 (Qdrant), all on 127.0.0.1 only. Inside Docker the standard ports are unchanged. Backend defaults and `.env.example` use these ports. The Postgres host port follows `CORTEX_DATABASE__PORT`.
* **Consequences**: Cortex runs side by side with other projects. Deployed environments are unaffected (they set URLs explicitly).

### ADR-018 — Windows host development: 127.0.0.1 and the selector event loop
* **Status**: Accepted (2026-11-26, TASK-01.06)
* **Context**: Two Windows-only problems surfaced while testing readiness against the live stack. (1) `localhost` resolves to `::1` first. Async clients then spend ~2 s per connection before falling back to IPv4, because compose binds IPv4 only. Measured: Postgres 10 s, Redis 2 s, versus 24 ms / 11 ms on `127.0.0.1`. (2) psycopg 3's async mode does not support the Proactor event loop that Windows and uvicorn use by default (`InterfaceError`).
* **Decision**: Local defaults and `.env.example` use `127.0.0.1`. Tests run AnyIO on `asyncio.SelectorEventLoop` on Windows (`tests/conftest.py`). Running uvicorn on Windows without `--reload` requires `--loop asyncio:SelectorEventLoop` (documented). Containers and CI (Linux) need neither.
* **Alternatives**: binding compose ports on `::1` as well (Docker Desktop IPv6 support varies); asyncpg instead of psycopg (works with Proactor, but loses the single sync+async driver from ADR-004).
* **Consequences**: Fast, reliable local runs on Windows. One extra flag in one documented situation.

### ADR-019 — Standard asyncio event loop in containers (not uvloop)
* **Status**: Accepted (2026-11-26, TASK-01.05)
* **Context**: In a dependency outage (Redis stopped, so Docker DNS cannot resolve `redis`), readiness checks time out and their DNS lookups are cancelled. Under uvloop each cancelled lookup was logged as "Future exception was never retrieved … gaierror". Measured in the same outage: uvloop 4 spurious lines, asyncio 0, with identical health results.
* **Decision**: The image runs `uvicorn … --loop asyncio`. Tests, Windows development and containers all use the standard loop.
* **Consequences**: Clean logs during incidents and one event-loop implementation everywhere. We give up uvloop's raw loop throughput, which matters little because latency is dominated by PostgreSQL, embedding and LLM calls. Revisit with Phase 14 load-test numbers (`uvicorn[standard]` still installs uvloop, so switching back is a one-flag change).

### ADR-020 — Conversation ownership is tied to organization membership
* **Status**: Accepted (2026-11-26, TASK-02.05); **confirmed final by the project owner on 2026-11-27** (keep deleting a removed member's conversations)
* **Context**: The design referenced `users` from `conversations.user_id`. That allows a conversation in an organization whose owner is not (or no longer) a member, so tenant access would rely only on application checks. Conversations are private to their creator.
* **Decision**: Composite FK `conversations (organization_id, user_id)` → `organization_memberships (organization_id, user_id)` ON DELETE CASCADE.
* **Consequences**: The database refuses non-member ownership. **Removing a member deletes their conversations in that organization.** Nobody else could read them, so this is data minimisation rather than loss of shared data. Alternatives considered and rejected: `ON DELETE RESTRICT` (removal blocked until the conversations are handled) and a soft "archived" state. Covered by `test_removing_a_member_deletes_their_conversations_adr_020`.

### ADR-021 — Argon2id parameters and password policy
* **Status**: Accepted (2026-11-26, TASK-03.01)
* **Context**: Password hashes must make offline guessing expensive without making logins slow or memory-hungry. pwdlib's default is Argon2id m=64 MiB, t=3, p=4. The policy must follow current guidance rather than habit.
* **Decision**: Argon2id with **m=19 MiB, t=2, p=1** (OWASP Password Storage Cheat Sheet minimum recommendation). Hashes are rehashed transparently on successful login when parameters change. Passwords are NFKC-normalised. Policy per NIST SP 800-63B: 12 to 128 characters, not only whitespace, not the email or its local part, and **no composition rules**. Hashing runs in a worker thread from async code. Unknown-email logins call `dummy_verify` so timing does not reveal which emails exist.
* **Measured** (dev machine): hash/verify ≈ 53 ms (library default ≈ 116 ms). Memory per concurrent hash: 19 MiB (20 parallel logins ≈ 380 MB, versus ≈ 1.3 GB at the default).
* **Consequences**: Strong against GPU cracking (memory-hard), affordable for the API. Raising the parameters later needs no migration: users upgrade on their next login. Not done (possible enhancement): checking new passwords against breached-password lists (e.g. the HIBP k-anonymity API).

### ADR-022 — Strict refresh-token reuse detection (no grace period)
* **Status**: Accepted (2026-11-27, TASK-03.03)
* **Context**: Refresh tokens rotate on every use. If an old (already replaced or revoked) token is presented, either it was stolen or the legitimate client sent the same token twice (two tabs refreshing at once). Some providers allow a short "reuse interval" to tolerate the second case.
* **Decision**: No grace period. Any reuse revokes the whole family (committed before the 401) and logs `auth.refresh_token_reuse_detected` (ids only). Unknown and reused tokens get the same response (`INVALID_REFRESH_TOKEN`), so an attacker learns nothing. Expired tokens return `REFRESH_TOKEN_EXPIRED`. Rows are locked with `SELECT … FOR UPDATE`, so concurrent use of one token is serialised: exactly one rotation succeeds, then the family is revoked (tested).
* **Consequences**: A stolen refresh token is useful only until the legitimate client next refreshes. The cost is that a client sending two concurrent refreshes logs itself out, so the SPA must run at most one refresh at a time (single-flight, TASK-12.02). Tokens are stored as SHA-256 digests (256-bit random values need no slow hash). Sessions slide: each rotation grants a fresh `refresh_token_ttl_days`. An absolute maximum session age is a possible later addition.

### ADR-023 — Stream uploads through python-multipart; detect type from bytes
* **Status**: Accepted (2026-11-27, TASK-04.03)
* **Context**: FastAPI's `UploadFile` parses the whole request body into a temporary file before the route runs, so a size limit can only be checked after the server has received everything. The client's `Content-Type` and file extension say nothing reliable about the content.
* **Decision**: The upload route reads `request.stream()` and feeds python-multipart's push parser (`app/uploads/receive.py`). A declared `Content-Length` over the limit is refused before reading; the file part is counted and hashed as it arrives (413 one byte over); the whole body is capped at limit + 64 KiB (multipart epilogues are otherwise unbounded). Exactly one part, named `file`, with a filename. The type is decided from content only (`app/uploads/detect.py`, standard library): `%PDF-1.x/2.x` header; DOCX = ZIP with the WordprocessingML main content type and `word/document.xml`, refusing macro-enabled files, templates, other Office formats, encrypted entries and declared zip bombs; text = strict UTF-8 without NUL/control characters (`.md` selects `text/markdown`). No libmagic.
* **Consequences**: Oversized uploads cost the server at most ~1 MiB of reading. Detection is predictable and dependency-free, but narrow: non-UTF-8 text (e.g. Latin-1) is refused with a clear reason. ZIP limits rely on declared sizes, so the DOCX parser (Phase 6) must also cap actual decompressed bytes. A PDF-header polyglot is stored as a PDF and always downloaded as an attachment.
* **Follow-up done (2026-11-28, TASK-06.02)**: the DOCX parser now caps the bytes *actually* decompressed (`app/rag/ingestion/archive.py`, 64 MiB for `document.xml`), so a lying ZIP directory no longer matters.

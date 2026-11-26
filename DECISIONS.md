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

### ADR-010 — Upload and delete permissions
* **Status**: Accepted (2026-11-26) (see P-3)
* **Decision**: MEMBER+ can upload and reprocess; delete allowed to the uploader or ADMIN+; KB management ADMIN+.

### ADR-011 — Redis sliding-window rate limiting implemented in-house
* **Status**: Accepted (2026-11-26)
* **Context**: Popular libraries add little over ~40 lines of Redis logic and some are poorly maintained.
* **Decision**: Small limiter (sorted-set or fixed-window-with-weight) used as a FastAPI dependency with per-route policies. Fail-closed on auth routes, fail-open elsewhere when Redis is down.
* **Consequences**: Fully understood and tested code; one less dependency.

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

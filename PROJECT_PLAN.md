# Cortex — Project Plan

> **Status: PLAN AWAITING APPROVAL.** No implementation starts until the plan is approved (TASK-00.07).
> Live task status lives in [TASK_TRACKER.md](TASK_TRACKER.md). This file defines *what* each task is and *when it is done*.

## 1. Goal

Build **Cortex**, a multi-tenant AI Knowledge & Research Engine: organizations create knowledge bases, upload PDF/DOCX/TXT/MD files, which are processed asynchronously into chunks, embedded (dense + sparse) and indexed in Qdrant; members ask questions and receive grounded answers with validated document/page citations; usage, cost and latency are tracked. Built as a portfolio-grade system you can defend line-by-line in a Backend + AI Engineer interview.

## 2. Scope: MVP vs optional

**MVP (must exist for the Definition of Done)**
* Auth (register/login/refresh/logout/me), orgs, memberships, OWNER/ADMIN/MEMBER RBAC, tenant isolation tests.
* KB CRUD; document upload with validation, hashing, per-KB dedup, storage adapter (local + S3), list/status/delete/reprocess.
* Celery ingestion with retries, idempotency, leases, reconciler; PDF/DOCX/TXT/MD extraction; cleaning; page-aware chunking.
* Dense (FastEmbed local default, OpenAI-compatible option) + sparse BM25 in Qdrant; embedding profiles/versioning.
* Weighted-RRF hybrid search + cross-encoder reranking.
* RAG: context builder, grounded prompt, OpenAI-compatible LLM, validated citations, "not in sources" behaviour, prompt-injection mitigations.
* Conversations + message history (non-streaming ask).
* Analytics API (usage/cost, processing stats, latency).
* Frontend: auth, dashboard, KBs, upload, documents, chat with citations, history, analytics, members/roles, account settings.
* Evaluation: ≥40 questions, Recall@5, MRR, citation correctness, faithfulness, dense vs hybrid comparison.
* Performance measurements recorded; Docker Compose for everything; CI (lint, types, tests, build).
* Production deployment docs + a costed deployment (deployment itself only after cost approval).

**Optional / stretch (scheduled only if MVP is on track)**
* SSE streaming answers (TASK-11.03) — high value, first stretch item.
* Prometheus + Grafana dashboards (TASK-13.02/13.03) — metrics endpoint is MVP-lite; dashboards stretch.
* KB reindex to a new embedding profile via API (TASK-07.08).
* Playwright end-to-end tests, organization deletion, email invitations, OCR for scanned PDFs, per-org token budgets, Qdrant server-side fusion comparison, monthly partitioning of `usage_events`.

**Explicitly out of scope**: SSO/OAuth login, billing/payments, real-time collaboration, fine-tuning models, mobile apps, multi-region.

## 3. Assumptions & prerequisites (found during environment inspection, 2026-11-26)

| Item | Found | Impact |
|---|---|---|
| D: drive | 118 GB free | OK |
| `D:\Projects\Cortex` | did not exist → created (empty apart from planning docs) | OK |
| Python 3.12 | available via `uv` (cpython 3.12.16); no system `python` on PATH | Use `uv` for everything (ADR-003) |
| Docker Desktop | 29.9.2, Compose v5.6.1, daemon running | OK |
| Docker memory | **~2.8 GB allocated** | **Risk**: PG + Redis + Qdrant + api + worker + ONNX models is tight. Recommend raising to **6–8 GB** in Docker Desktop → Settings → Resources before Phase 5. |
| Node.js / npm | **not installed** | **Blocker for Phase 12**: install Node 22 LTS before TASK-12.01. |
| Git | 2.56 installed; repo not initialised yet | Done in TASK-01.01 |
| LLM API key | unknown | **Needed by Phase 10.** Any OpenAI-compatible endpoint works (OpenAI, Anthropic's OpenAI-compatible endpoint, OpenRouter, Groq, or a local Ollama). Tests use a fake provider so CI never needs a key. |
| AWS account | unknown | Needed only for Phase 16, after cost approval. |

## 4. Phases

Effort is in focused hours. Total MVP ≈ 175 h; at ~6 h/day that is ~30 working days, which is **tight but realistic only if stretch items stay deferred**. Status values: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `COMPLETED`, `NEEDS_REVIEW`.

---

### PHASE-00 — Project planning and architecture
* **Objective**: Agree on architecture, data model, API, plan and working process before code exists.
* **Technical requirements**: none beyond documentation.
* **Dependencies**: none.
* **Expected output**: the 11 root planning documents + `docs/` structure.
* **Testing**: n/a (review by you).
* **Effort**: 6 h · **Status**: NEEDS_REVIEW

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-00.01 | Inspect environment: D: drive, existing project, toolchain (Python/uv, Docker, Node, Git) | Findings recorded in §3 and SESSION_LOG | 0.5 |
| TASK-00.02 | Analyse requirements; split MVP vs optional; list out-of-scope | §2 reviewed by you | 1 |
| TASK-00.03 | Architecture proposal (style, layout, layering, async model, ingestion, RAG, security, observability, deployment options) | ARCHITECTURE.md approved | 1.5 |
| TASK-00.04 | Database design (tables, constraints, indexes, integrity rules) | DATABASE_DESIGN.md approved | 1 |
| TASK-00.05 | API design (endpoints, roles, errors, pagination, rate limits) | API_SPECIFICATION.md approved | 1 |
| TASK-00.06 | Plan, tracker, schedule, risks, guidelines, ADRs, logs, README | All planning files exist and are consistent | 1 |
| TASK-00.07 | **Plan approval** (you) — incl. open decisions in DECISIONS.md "Pending your input" | Your explicit approval in chat; recorded in SESSION_LOG | — |

---

### PHASE-01 — Development environment and repository setup
* **Objective**: A reproducible dev environment where `docker compose up` gives healthy PG/Redis/Qdrant and an empty FastAPI app, with lint/type/test running locally and in CI.
* **Technical requirements**: uv-managed Python 3.12, ruff, mypy, pytest; Compose with health checks; pinned image versions; `.env.example`.
* **Dependencies**: PHASE-00 approved.
* **Expected output**: Git repo, `backend/` skeleton, `infra/compose/docker-compose.yml`, Dockerfile, CI workflow.
* **Testing**: health endpoint tests; CI green on first push.
* **Effort**: 9 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-01.01 | `git init`, `.gitignore` (Python/Node/.env/uploads), `.gitattributes` (LF for sh, CRLF-safe), `.editorconfig`, initial commit of planning docs | `git status` clean; `.env` patterns ignored (verified with `git check-ignore`) | 0.5 |
| TASK-01.02 | Backend scaffold: `uv init`, Python pinned 3.12, package `app` with the agreed module tree, ruff + mypy + pytest config in `pyproject.toml` | `uv run ruff check`, `uv run mypy app`, `uv run pytest` all run and pass on the skeleton | 1.5 |
| TASK-01.03 | Settings (`pydantic-settings`): env profiles, nested settings groups (db, redis, qdrant, storage, auth, rag, llm), production validation; `.env.example` | Unit tests: production settings with default secret → error; dev defaults load; every setting appears in `.env.example` (test enforces) | 1.5 |
| TASK-01.04 | Docker Compose (dev): postgres:17, redis:7, qdrant (pinned ≥1.12), health checks, named volumes, ports bound to 127.0.0.1 | `docker compose up -d` → all services `healthy` | 1 |
| TASK-01.05 | Backend Dockerfile (multi-stage, uv, non-root user, no dev deps) + `api` service in compose | Image builds; container passes health check; runs as non-root (`whoami` ≠ root) | 1.5 |
| TASK-01.06 | App factory, `/health/live`, `/health/ready` (PG, Redis, Qdrant checks with timeouts) | Tests for both; ready returns 503 when a dependency is stopped (manually verified + test with fake) | 1 |
| TASK-01.07 | Structured logging: structlog JSON, request-id middleware, access log, redaction processor | Test: log record containing `password`/`authorization` is masked; `X-Request-ID` echoed | 1 |
| TASK-01.08 | Dev scripts (`scripts/dev.ps1`: up, down, logs, test, migrate, reset — reset asks for confirmation) | Each command documented in docs/development and works on Windows | 0.5 |
| TASK-01.09 | GitHub Actions CI: ruff, mypy, pytest with PG/Redis/Qdrant service containers, Docker build | Workflow green on GitHub (requires remote — if none, validated with `act` or deferred and marked BLOCKED) | 0.5 |

---

### PHASE-02 — Database design and backend foundation
* **Objective**: All core tables migrated, plus the cross-cutting foundations every feature uses (session/UoW, errors, pagination, test harness).
* **Technical requirements**: SQLAlchemy 2.0 async (psycopg 3), Alembic async env, naming conventions, keyset pagination, error envelope.
* **Dependencies**: PHASE-01.
* **Expected output**: models, 3 migrations, repository base, error handlers, test fixtures.
* **Testing**: migration round-trip test, repository tests against real PG, error-format tests.
* **Effort**: 12 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-02.01 | DB engine/session factory, `Base` with naming convention, UUID + timestamp mixins, session dependency | Session opens/closes per request (test); no connection leak after 100 requests (pool check) | 1.5 |
| TASK-02.02 | Alembic async setup; CI migration round-trip test (`upgrade head → downgrade base → upgrade head`) | Round-trip test passes | 1.5 |
| TASK-02.03 | Models + migration: users, organizations, organization_memberships, refresh_tokens | Migration applies; constraints verified by tests (duplicate email case-insensitive rejected, duplicate membership rejected) | 2 |
| TASK-02.04 | Models + migration: knowledge_bases, documents, document_chunks, processing_jobs incl. composite tenant FKs and partial unique indexes | Tests prove: cross-org document insert rejected by FK; second active job rejected; duplicate content_hash in same KB rejected | 2.5 |
| TASK-02.05 | Models + migration: conversations, messages, message_citations, usage_events | Migration applies; cascade behaviour tested | 1.5 |
| TASK-02.06 | Domain exceptions + global handlers + error envelope; 404/405/422/500 normalized | Tests for each error class; 500 response contains no stack trace | 1 |
| TASK-02.07 | Keyset pagination helper (opaque cursor encode/decode, tamper-safe validation) | Unit tests: stable ordering with equal timestamps; invalid cursor → 400 | 1 |
| TASK-02.08 | Test harness: per-session test DB, migrations once, per-test transaction rollback (savepoints), factories, `AsyncClient` fixture | Test suite runs in isolation and in any order (`pytest -p randomly`) | 1 |

---

### PHASE-03 — Authentication and multi-tenancy
* **Objective**: Secure auth and org-scoped RBAC that every later endpoint plugs into.
* **Technical requirements**: Argon2id, JWT access tokens, rotating hashed refresh tokens in HttpOnly cookie, org context dependency, role checks, Redis rate limiting.
* **Dependencies**: PHASE-02.
* **Expected output**: auth + org + member endpoints; `OrgContext` dependency; security model doc; tenant-isolation test suite skeleton.
* **Testing**: unit (hashing, jwt), integration (all auth flows incl. refresh reuse), RBAC matrix, isolation tests, rate-limit tests.
* **Effort**: 16 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-03.01 | Password hashing service (Argon2id, rehash-on-login if params change), password policy | Unit tests: verify, wrong password, policy violations | 1 |
| TASK-03.02 | JWT issue/verify (claims, expiry, leeway, `typ` check, algorithm pinned) | Tests: expired, tampered, wrong `typ`, `alg=none` all rejected | 1.5 |
| TASK-03.03 | Refresh tokens: generation, SHA-256 storage, rotation, reuse detection → family revoke, cookie settings | Tests: rotation works; replaying old token returns 401 and revokes the newer one | 2.5 |
| TASK-03.04 | Endpoints: register (user + org + OWNER membership in one transaction), login, refresh, logout, me, update me, change password | Integration tests for every path incl. 409 duplicate email, generic login failure | 3 |
| TASK-03.05 | `get_current_user`, `OrgContext` (membership lookup → 404 for non-members), `require_role(min_role)` | Tests: non-member 404, MEMBER on ADMIN route 403, inactive user 401 | 1.5 |
| TASK-03.06 | Org endpoints + member management (add existing user, change role, remove, leave) with last-owner protection | Tests for RBAC matrix rows; concurrent demotion of two owners cannot leave zero owners (locking test) | 3 |
| TASK-03.07 | Redis sliding-window rate limiter as dependency; applied to login/register | Tests: limit hit → 429 + `Retry-After`; fail-closed on auth when Redis down | 1.5 |
| TASK-03.08 | Tenant-isolation test framework: two orgs fixture + parametrised "user B probes org A" test over a route registry | Test fails if a new org-scoped route is added without an isolation case | 1 |
| TASK-03.09 | `docs/architecture/security-model.md` (threat model, token lifecycle, tenancy, RBAC) | Doc reviewed; matches implementation | 1 |

---

### PHASE-04 — Document upload and storage
* **Objective**: Users can create KBs and upload validated documents that are stored safely and recorded in PG.
* **Technical requirements**: Storage protocol (local + S3), streamed upload with size cap, magic-byte detection, SHA-256, per-KB dedup, quotas.
* **Dependencies**: PHASE-03.
* **Expected output**: KB + document endpoints (without processing yet — documents stay `PENDING`).
* **Testing**: storage unit tests (local + moto S3), upload validation tests, dedup race test, isolation cases.
* **Effort**: 11 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-04.01 | `StorageBackend` protocol; `LocalStorage` (root-confined, path-traversal safe); `S3Storage` (boto3, SSE, presigned GET) | Same contract test suite passes for both (S3 via `moto`); traversal attempts rejected | 2.5 |
| TASK-04.02 | KB service/repository/routes (CRUD, soft delete, stats) | Integration + RBAC + isolation tests | 2 |
| TASK-04.03 | Upload validation: streamed size limit (413 without buffering whole file), type detection from bytes (PDF header, DOCX zip structure, UTF-8 text), filename sanitization, org quota | Tests with oversized, renamed-exe-as-pdf, polyglot, empty and non-UTF-8 files | 2 |
| TASK-04.04 | Streamed SHA-256 + dedup within KB (pre-check + unique-index race handling → 409 with existing id; orphan object removed) | Test: two concurrent identical uploads → exactly one document | 1.5 |
| TASK-04.05 | Upload endpoint creates document `PENDING` + `processing_job QUEUED` in one transaction → 202 | Integration test; DB state verified | 1 |
| TASK-04.06 | Document list (filter/search/paginate), get, status, download (stream or presigned) | Integration + isolation tests | 2 |

---

### PHASE-05 — Background processing infrastructure
* **Objective**: Reliable async job execution: claimed exactly once, retried on transient failures, recovered after crashes, safely deleted.
* **Technical requirements**: Celery + Redis, `acks_late`, prefetch 1, time limits, retry with backoff/jitter, leases, beat reconciler, post-commit enqueue.
* **Dependencies**: PHASE-04.
* **Expected output**: worker + beat services; job state machine; delete/reprocess flows. The real pipeline stages arrive in Phases 6–7; Phase 5 tests inject a test pipeline. Nothing ships that marks a document READY without actually processing it.
* **Testing**: worker tests (retry, permanent failure, idempotent double-delivery, lease expiry recovery, lost-enqueue recovery).
* **Effort**: 11 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-05.01 | Celery app: config, JSON serializer, `acks_late`, `task_reject_on_worker_lost`, prefetch=1, soft/hard time limits, per-process DB engine init, structured logging with task id | Worker boots in compose and logs JSON; health ping task test | 1.5 |
| TASK-05.02 | Job state machine + atomic claim (`UPDATE … WHERE status IN (…) RETURNING`), lease set/renew | Test: two concurrent claims → one winner | 2 |
| TASK-05.03 | Task base: `TransientError` vs `PermanentError`, exponential backoff + jitter, max attempts, error recorded on job/document | Tests: transient → retried then succeeds; permanent → FAILED with code, no retry | 2 |
| TASK-05.04 | Post-commit enqueue; beat reconciler: re-enqueue stale QUEUED, recover expired RUNNING leases, fail after max attempts | Tests simulate lost enqueue and crashed worker; both recover | 2 |
| TASK-05.05 | Reprocess endpoint; delete flow (DELETING → Qdrant purge → storage delete → row delete), KB delete cascade task | Tests: deleted doc invisible immediately; cleanup idempotent when run twice | 2 |
| TASK-05.06 | Compose services `worker`, `beat`; graceful shutdown; docs in docs/development | `docker compose up` runs all; SIGTERM lets running task finish/requeue | 1.5 |

---

### PHASE-06 — Document extraction and chunking
* **Objective**: Turn each supported file into clean text with page/section metadata and deterministic, well-sized chunks stored in PG.
* **Technical requirements**: pypdf (BSD licence; PyMuPDF avoided because of AGPL — ADR-009), python-docx, charset-normalizer, tiktoken for token counting.
* **Dependencies**: PHASE-05.
* **Expected output**: parsers, cleaner, chunker, pipeline stages 1–3, fixture corpus.
* **Testing**: parser unit tests per format, chunker property tests (no text lost, overlap respected, page spans correct), malicious-file tests.
* **Effort**: 12 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-06.01 | Parser protocol → `ParsedDocument(pages|sections)`; PDF parser per page; encrypted/empty/scanned detection (`PDF_ENCRYPTED`, `EXTRACTION_EMPTY`) | Tests on fixture PDFs incl. multi-column, encrypted, image-only | 2 |
| TASK-06.02 | DOCX parser: paragraphs, headings → section path, tables → row text; zip-bomb guard (uncompressed size / ratio limits) | Tests incl. bomb fixture rejected | 1.5 |
| TASK-06.03 | TXT/MD parser: encoding detection, Markdown heading paths, code blocks kept intact | Tests: UTF-8, UTF-16, cp1252 inputs | 1 |
| TASK-06.04 | Cleaner: NFKC, control chars, de-hyphenation across line breaks, whitespace normalisation, repeated header/footer removal; keeps offset map | Unit tests with before/after fixtures | 1.5 |
| TASK-06.05 | Chunker: structure-aware recursive splitting (section → paragraph → sentence → tokens) with token size/overlap, page_start/page_end from offsets, section_path, deterministic ids | Property tests: concatenation covers all text; every chunk ≤ size; same input ⇒ same chunk ids | 3 |
| TASK-06.06 | Pipeline stages extract → clean → chunk → replace chunks in one PG transaction; job `stage` + metrics updates | Integration test: upload fixture → chunks in DB with correct pages | 1.5 |
| TASK-06.07 | Resource limits: max pages, max extracted chars, per-task time limit; clear error codes | Tests for each limit | 1.5 |

---

### PHASE-07 — Embedding generation and Qdrant integration
* **Objective**: Chunks are embedded (dense + sparse) and indexed idempotently in a versioned Qdrant collection; documents become READY.
* **Technical requirements**: FastEmbed (dense bge-small, sparse BM25), OpenAI-compatible embedding provider, Qdrant named dense + sparse vectors, payload indexes, batching.
* **Dependencies**: PHASE-06.
* **Expected output**: embedding providers, profile registry, vector index wrapper, full ingestion pipeline, consistency reconciler.
* **Testing**: provider contract tests (fake/local), Qdrant integration tests, idempotency (re-run → same point count), reconciler tests.
* **Effort**: 12 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-07.01 | `EmbeddingProvider` protocol; FastEmbed dense provider; OpenAI-compatible provider (batching, timeouts, retry on 429/5xx); deterministic `FakeEmbedder` | Contract tests pass for fake + local; OpenAI-compatible tested with mocked HTTP (respx) | 2.5 |
| TASK-07.02 | Embedding profiles (config registry → collection name, dims); KB pinned to profile; startup check that model dims match collection | Test: mismatched dims refuses to start/index | 1 |
| TASK-07.03 | Vector index wrapper: ensure collection (dense cosine + sparse `idf`), payload indexes (`organization_id` tenant, `knowledge_base_id`, `document_id`), batch upsert, delete-by-filter, count-by-filter; **refuses searches without tenant filter** | Integration tests against Qdrant container | 2.5 |
| TASK-07.04 | Sparse encoder (FastEmbed `Qdrant/bm25`) | Unit tests; sparse vectors non-empty for normal text | 1 |
| TASK-07.05 | Pipeline stages embed → index → finalize (chunks `indexed_at`, doc READY, job SUCCEEDED, usage_event); stale-point cleanup when chunk count shrinks | E2E test: upload → READY with N points; reprocess → still N points | 2.5 |
| TASK-07.06 | Consistency reconciler: per-document PG vs Qdrant counts, orphan points (doc missing in PG) purge | Test: manually injected orphan/missing points repaired | 1.5 |
| TASK-07.07 | Ingestion benchmark script (pages/s, chunks/s, embedding time) → docs/testing/ingestion-benchmark.md | Numbers recorded with hardware description | 1 |
| TASK-07.08 | *(stretch)* KB reindex to new embedding profile (shadow collection, flip on completion) | Test: queries served from old profile until flip | — |

---

### PHASE-08 — Dense and sparse retrieval
* **Objective**: Authorized, tenant-filtered dense and sparse search returning hydrated chunks.
* **Dependencies**: PHASE-07.
* **Expected output**: retrievers, hydration, debug search endpoint.
* **Testing**: retrieval correctness on fixture corpus; vector-level tenant leakage tests.
* **Effort**: 6 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-08.01 | Authorization step: resolve searchable KB ids from PG for `OrgContext`; build mandatory filter | Unit test: KB from other org never appears in filter | 1 |
| TASK-08.02 | Dense retriever (query embedding in threadpool, `query_points` with filter, top_k) | Integration test: known question → expected chunk in top 5 | 1.5 |
| TASK-08.03 | Sparse retriever | Same, with keyword-heavy query (codes/IDs) where dense is weak | 1 |
| TASK-08.04 | Hydration from PG (`organization_id` re-check, exclude DELETING docs) | Test: hit for a doc in DELETING is dropped | 1 |
| TASK-08.05 | `POST /orgs/{org}/search` (ADMIN) returning scores + stage latencies | Integration + isolation tests | 1.5 |

---

### PHASE-09 — Hybrid search and reranking
* **Objective**: Better ranking via weighted RRF fusion and cross-encoder reranking, all configurable and timed.
* **Dependencies**: PHASE-08.
* **Effort**: 7 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-09.01 | Weighted RRF (`score = Σ wᵢ / (k + rankᵢ)`), dedupe, tie-breaking | Unit tests with hand-computed expected rankings | 1.5 |
| TASK-09.02 | `Reranker` protocol; cross-encoder (FastEmbed `TextCrossEncoder`, threadpool, batch); `NoOpReranker` | Unit tests; integration on fixture shows reranker changes order sensibly | 2 |
| TASK-09.03 | `RetrievalPipeline` with config (top_k_dense, top_k_sparse, fused_k, rerank_top_n, final_k, min_score, weights) per request/default | Tests for each config path (dense-only, sparse-only, hybrid, ±rerank) | 2 |
| TASK-09.04 | Stage timing + Prometheus histograms + trace object | Trace contains per-stage ms; metrics exposed | 1.5 |

---

### PHASE-10 — RAG pipeline and LLM integration
* **Objective**: Grounded answers with validated citations, honest "not found", resistant to document prompt injection, with cost tracking.
* **Dependencies**: PHASE-09, LLM endpoint/API key (for manual runs only).
* **Effort**: 12 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-10.01 | `LLMProvider` protocol; OpenAI-compatible provider (timeouts, retries, usage capture, streaming iterator); `FakeLLM` (scripted) | Contract tests with respx mocks; fake used in all automated tests | 2 |
| TASK-10.02 | Context builder: token budget, ordering by score, numbered `[S1..Sn]` blocks with filename/page header, untrusted-content delimiters, neutralisation of delimiter look-alikes inside text | Unit tests: budget respected; injected fake delimiters escaped | 2 |
| TASK-10.03 | Prompt templates (versioned constants): grounding rules, citation format, exact "not found" sentence, injection policy | Prompt version stored on message; snapshot test of rendered prompt | 1 |
| TASK-10.04 | Answer generator + citation mapper: parse `[S#]`, keep only markers that were supplied, map to chunk/doc/page/snippet, flag `no_valid_citations` | Tests: fabricated `[S9]` dropped; answer with no citations flagged; "not found" path | 2 |
| TASK-10.05 | Follow-up question condensation using last N turns (skipped for first turn) | Test with fake LLM; standalone query stored | 1.5 |
| TASK-10.06 | Price table config + usage events for query embedding, rewrite, completion; cost calculation with `Decimal` | Unit tests for cost math | 1 |
| TASK-10.07 | Prompt-injection test fixtures (doc saying "ignore previous instructions…", "reveal system prompt", fake citations) + manual run against real LLM recorded in docs/testing | Automated tests assert pipeline-level defences; manual results documented honestly | 2.5 |

---

### PHASE-11 — Chat and conversation management
* **Objective**: Persisted conversations with history-aware question answering.
* **Dependencies**: PHASE-10.
* **Effort**: 8 h (+3 h stretch) · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-11.01 | Conversation CRUD (private to creator), list by `updated_at` | Integration + isolation tests (same-org other user also 404) | 2 |
| TASK-11.02 | Ask endpoint: persist user message → pipeline → persist assistant message + citations + latencies + trace; failure path stores FAILED message and returns 503 `LLM_UNAVAILABLE` | Integration tests with fake LLM; DB rows verified | 3 |
| TASK-11.03 | *(stretch)* SSE streaming (`retrieval`, `token`, `citations`, `done`, `error` events), client disconnect handling | Test with httpx stream; message persisted after stream ends | (3) |
| TASK-11.04 | History endpoint (paginated, citations embedded); delete conversation | Tests | 1.5 |
| TASK-11.05 | Rate limit on ask; max question length; per-conversation message cap | Tests | 1.5 |

---

### PHASE-12 — Frontend development
* **Objective**: A clean, responsive SaaS UI over the full API.
* **Technical requirements**: React + TS + Vite, Tailwind, shadcn/ui, Zustand, React Router, generated OpenAPI types, Vitest + Testing Library.
* **Dependencies**: Node 22 LTS installed (**currently missing**); API phases 3–11.
* **Effort**: 26 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-12.01 | Install Node LTS; scaffold Vite React-TS; Tailwind; shadcn init; ESLint/Prettier; Vitest; frontend Dockerfile (Nginx) | `npm run build`, `npm test`, `npm run lint` pass | 2 |
| TASK-12.02 | API layer: `openapi-typescript` types, fetch client with auth header, 401 → single-flight refresh → retry, error normalisation; service modules per resource | Unit tests for refresh queueing and error mapping | 3 |
| TASK-12.03 | Zustand stores: auth (access token in memory), current org, UI/toasts | Store unit tests | 1.5 |
| TASK-12.04 | App shell: router, protected routes, sidebar, org switcher, toasts, error boundary, loading/empty/error components | Renders on mobile + desktop widths | 2.5 |
| TASK-12.05 | Login & registration pages with validation | Component tests | 2 |
| TASK-12.06 | Dashboard (KB count, docs by status, recent conversations, usage snapshot) | Renders with real API | 2 |
| TASK-12.07 | Knowledge bases list/create/edit/delete + detail page | Works end-to-end | 2.5 |
| TASK-12.08 | Upload (drag-and-drop, multi-file, progress, validation messages) + document table with live status polling, reprocess, delete | Upload → status reaches READY in UI | 3 |
| TASK-12.09 | Chat UI: conversation sidebar/history, message list, composer, citation chips + source panel (file, pages, snippet), "not found" styling | Ask → answer with clickable citations | 4 |
| TASK-12.10 | Analytics page (usage/cost, processing, latency charts) | Shows real data | 2 |
| TASK-12.11 | Org settings + members/roles management; account settings (name, password) | Role-restricted controls hidden/disabled per role | 2 |
| TASK-12.12 | Frontend tests for services, stores and key components; *(stretch)* Playwright smoke | Coverage of services/stores ≥ 80% | (incl.) |

---

### PHASE-13 — Usage analytics and monitoring
* **Objective**: Answer "what does this cost, how fast is it, what is failing" from both API and dashboards.
* **Dependencies**: PHASE-11.
* **Effort**: 6 h (+3 h stretch) · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-13.01 | Analytics service + endpoints (usage/cost, documents, retrieval latency percentiles via `percentile_cont`) | Integration tests with seeded events; ADMIN-only | 3 |
| TASK-13.02 | Prometheus metrics: HTTP, retrieval stages, LLM latency/tokens, Celery task duration/outcome, queue depth; `/metrics` | Metrics visible via curl; names documented | 2 |
| TASK-13.03 | *(stretch)* Compose `monitoring` profile with Prometheus + provisioned Grafana dashboard | Dashboard JSON committed; screenshot in docs | (3) |
| TASK-13.04 | Seed script generating realistic demo data for analytics | Script idempotent | 1 |

---

### PHASE-14 — Testing and performance optimization
* **Objective**: Prove retrieval quality and performance with numbers.
* **Dependencies**: PHASE-11 (eval needs full pipeline); LLM key for faithfulness judging.
* **Effort**: 14 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-14.01 | Evaluation corpus from public-domain/permissively licensed docs (e.g. US government publications, Python PEPs, RFCs) — mixed PDF/DOCX/MD; manifest with licence per file | ≥8 documents, ≥200 pages total, licences recorded | 2 |
| TASK-14.02 | Question set: ≥40 questions (factual, multi-hop, keyword/ID, paraphrase, ≥5 unanswerable) with gold document + page(s) | Reviewed JSONL in `eval/` | 3 |
| TASK-14.03 | Retrieval eval runner: Recall@5, Recall@10, MRR, per-mode (dense, sparse, hybrid, hybrid+rerank), latency | Report in docs/testing/retrieval-evaluation.md with tables | 3 |
| TASK-14.04 | Answer eval: citation correctness (cited page ∈ gold pages), faithfulness (LLM-as-judge rubric, with caveats), unanswerable refusal rate | Report section with numbers and limitations | 2 |
| TASK-14.05 | Load test (Locust): login, list, search, ask (fake LLM vs real LLM separately); record p50/p95/p99 and throughput | docs/testing/performance.md with hardware + config | 2 |
| TASK-14.06 | DB analysis: `pg_stat_statements`, `EXPLAIN ANALYZE` hot queries, fix issues found, before/after numbers | Documented; no unexplained seq scans on hot paths | 1 |
| TASK-14.07 | Coverage gate (backend ≥ 80% lines overall, services/rag ≥ 85%) enforced in CI | CI fails below threshold | 1 |

---

### PHASE-15 — Security and production readiness
* **Dependencies**: PHASE-13.
* **Effort**: 7 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-15.01 | OWASP API Top-10 review against code; findings fixed or recorded | docs/architecture/security-review.md | 2 |
| TASK-15.02 | Dependency & secret scanning: `pip-audit`, `npm audit`, gitleaks in CI | CI jobs green; no high/critical unaddressed | 1 |
| TASK-15.03 | Security headers, strict CORS per env, trusted hosts, max request body, OpenAPI docs policy in prod | Tests assert headers and CORS behaviour | 1.5 |
| TASK-15.04 | File-processing & prompt-injection risk review documented with mitigations and residual risks | Doc reviewed | 1 |
| TASK-15.05 | Backup/restore runbook: PG dump/restore, Qdrant snapshot/restore, S3 versioning; tested locally | Restore drill performed and logged | 1.5 |

---

### PHASE-16 — Production deployment (**approval gates**)
* **Dependencies**: PHASE-15; AWS account; your cost approval; domain (optional).
* **Effort**: 12 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-16.01 | Deployment design + itemised monthly cost estimate (option A lean vs B reference) | **Your written approval** of option and budget | 2 |
| TASK-16.02 | Production images (GHCR), prod compose/Caddy or chosen IaC, secrets via SSM, S3 bucket policy, RDS | `docs/deployment/production.md` complete | 3 |
| TASK-16.03 | Staging environment (separate DB, credentials, bucket) deployed via pipeline | Smoke tests pass on staging | 3 |
| TASK-16.04 | GitHub Actions deploy workflow: build → push → migrate → deploy, manual approval for production | Successful staging deploy from CI | 2 |
| TASK-16.05 | Production deploy, post-deploy smoke tests, rollback procedure rehearsed | **Separate approval** before running; smoke tests pass | 2 |

---

### PHASE-17 — Documentation and portfolio preparation
* **Dependencies**: all.
* **Effort**: 6 h · **Status**: NOT_STARTED

| ID | Task (subtasks) | Completion condition | h |
|---|---|---|---|
| TASK-17.01 | README final pass (setup verified from a fresh clone) | Fresh-clone setup works following README only | 1.5 |
| TASK-17.02 | docs: RAG pipeline deep-dive, evaluation results, troubleshooting | Docs match code | 2 |
| TASK-17.03 | Architecture diagrams final, demo script, interview talking points (trade-offs, failures, numbers) | docs/portfolio.md | 1.5 |
| TASK-17.04 | Tracker audit: every COMPLETED task has tests + evidence | Tracker matches repo | 1 |

## 5. Main technical risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| R1 | **30-day timeline** vs ~175 h MVP | High / High | Strict MVP; stretch items only after Day 25 checkpoint; frontend kept functional not fancy. |
| R2 | Docker memory (2.8 GB) with PG+Redis+Qdrant+api+worker+ONNX models | High / Med | Raise to 6–8 GB; models loaded once per process; worker concurrency 2 in dev. |
| R3 | Node not installed | Certain / Med | Install before Phase 12 (does not block backend). |
| R4 | LLM key / cost | Med / Med | OpenAI-compatible abstraction; fake LLM in tests; cheap model default; local Ollama possible; price table + usage tracking. |
| R5 | PDF extraction quality (multi-column, tables, scanned) | Med / Med | Page-level extraction + cleaner; scanned PDFs detected and failed with clear code (OCR is stretch). |
| R6 | PG ↔ Qdrant inconsistency | Med / High | Deterministic ids, idempotent stages, reconciler, consistency tests. |
| R7 | Cross-tenant leakage | Low / Critical | Path-claim + membership, org-scoped repos, composite FKs, mandatory vector filter, isolation suite enforced for every route. |
| R8 | Prompt injection via documents | Med / Med | Delimited untrusted context, system rules, citation validation, no tools/actions available to the LLM (limits blast radius); residual risk documented honestly. |
| R9 | CPU-bound inference blocking API event loop | Med / Med | Threadpool offload; measure in load tests; separate inference process is the documented escape hatch. |
| R10 | Windows-specific friction (line endings, file paths, Celery on Windows) | Med / Low | Run worker only in Linux containers (Celery doesn't support Windows natively); `.gitattributes`; pathlib everywhere. |
| R11 | AWS costs | Low / Med | Approval gate, lean option, budgets/alarms configured first. |

## 6. 30-day execution schedule

Assumes ~6 focused hours/day. Each day ends with tests green + tracker + session log updated.

| Day | Work | Milestone |
|---|---|---|
| 1 | PHASE-00 review/approval; TASK-01.01–01.04 | Repo + infra up |
| 2 | TASK-01.05–01.09 | **M1: dev env + CI green** |
| 3 | TASK-02.01–02.04 | Core schema migrated |
| 4 | TASK-02.05–02.08 | **M2: backend foundation** |
| 5 | TASK-03.01–03.03 | Tokens |
| 6 | TASK-03.04–03.05 | Auth endpoints |
| 7 | TASK-03.06–03.09 | **M3: auth + tenancy + isolation suite** |
| 8 | TASK-04.01–04.03 | Storage + KBs |
| 9 | TASK-04.04–04.06 | **M4: upload works** |
| 10 | TASK-05.01–05.03 | Workers + retries |
| 11 | TASK-05.04–05.06 | **M5: reliable jobs** |
| 12 | TASK-06.01–06.04 | Parsers + cleaner |
| 13 | TASK-06.05–06.07 | **M6: chunks in PG** |
| 14 | TASK-07.01–07.04 | Embeddings + Qdrant |
| 15 | TASK-07.05–07.07 | **M7: documents READY end-to-end** |
| 16 | PHASE-08 | Dense + sparse search |
| 17 | TASK-09.01–09.02 | Fusion + rerank |
| 18 | TASK-09.03–09.04; buffer | **M8: hybrid retrieval** |
| 19 | TASK-10.01–10.04 | Grounded answers |
| 20 | TASK-10.05–10.07 | **M9: RAG complete** |
| 21 | PHASE-11 (non-stream) | **M10: backend MVP feature-complete** |
| 22 | TASK-12.01–12.04 | Frontend shell |
| 23 | TASK-12.05–12.07 | Auth + KBs UI |
| 24 | TASK-12.08–12.09 | Upload + chat UI |
| 25 | TASK-12.10–12.12; **scope checkpoint** | **M11: UI functional** |
| 26 | PHASE-13 (MVP parts) + TASK-14.01–14.02 | Analytics + eval set |
| 27 | TASK-14.03–14.07 | **M12: numbers recorded** |
| 28 | PHASE-15 | Security hardening |
| 29 | TASK-16.01 (cost approval) → 16.02–16.04 | Staging |
| 30 | TASK-16.05 (if approved) + PHASE-17 | **M13: Definition of Done** |

Slack is thin. If a day slips, the order of things to cut is: Playwright → Grafana dashboards → SSE streaming → production deploy (staging + documented prod plan still satisfies "production deployment documentation exists").

## 7. What to build first (recommendation)

Build the **vertical spine before breadth**: PHASE-01 → 02 → 03 exactly as planned, because tenant isolation and the test harness are expensive to retrofit and every later endpoint depends on `OrgContext`, the error envelope and the transactional test fixtures. The first concrete task after approval is **TASK-01.01** (git init + ignore rules), then TASK-01.02–01.04 so infrastructure is healthy before any application code.

## 8. Definition of Done (project)

See the task list above; the project is done when every MVP task is COMPLETED in TASK_TRACKER.md with evidence, and all items of the original Definition of Done hold: runs locally, auth, multi-tenant access control, uploads, background processing, embeddings stored, hybrid retrieval, valid citations, conversation history, functional frontend, tests pass, tenant isolation tested, performance metrics recorded, Docker deployment works, production deployment documentation exists, professional documentation, accurate tracker.

# Cortex — AI Knowledge & Research Engine

> **Project status: planning complete, implementation not started.** Sections marked *(planned)* describe the target state and will be verified as each phase lands. See [TASK_TRACKER.md](TASK_TRACKER.md) for live progress.

Cortex is a multi-tenant SaaS platform where organizations build private knowledge bases from their documents and ask questions that are answered **only from those documents, with verifiable citations down to the page**.

## Main features *(planned)*
- Organizations, members and OWNER / ADMIN / MEMBER roles with strict tenant isolation
- Multiple knowledge bases per organization
- Upload PDF, DOCX, TXT and Markdown; validated, de-duplicated, stored in S3 (local disk in dev)
- Asynchronous, retry-safe ingestion: extraction → cleaning → page-aware chunking → embeddings → Qdrant
- Hybrid retrieval (dense + BM25 sparse, weighted RRF) with cross-encoder reranking
- Grounded answers from any OpenAI-compatible LLM, with validated `[S#]` citations and honest "not found"
- Conversation history, processing status, usage/cost/latency analytics
- Retrieval evaluation harness (Recall@5, MRR, citation correctness, faithfulness)

## Architecture overview
Modular monolith (FastAPI) + Celery workers, PostgreSQL as source of truth, Qdrant as derived vector index, Redis for queues and rate limits, React SPA. Details: [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE_DESIGN.md](DATABASE_DESIGN.md) · [API_SPECIFICATION.md](API_SPECIFICATION.md) · [DECISIONS.md](DECISIONS.md).

## Technology stack
Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · PostgreSQL · Qdrant · Celery · Redis · FastEmbed (ONNX) · React · TypeScript · Vite · Tailwind · shadcn/ui · Zustand · Docker Compose · GitHub Actions · AWS. Rationale: [TECH_STACK.md](TECH_STACK.md).

## Prerequisites
- Windows 10/11, macOS or Linux
- Docker Desktop with **6–8 GB RAM** allocated
- [uv](https://docs.astral.sh/uv/) (manages Python 3.12)
- Node.js 22 LTS (frontend)
- An OpenAI-compatible LLM endpoint + key (only for real answers; tests use fakes)

## Local setup
```powershell
git clone https://github.com/Snsnehad/cortex.git Cortex
cd Cortex
Copy-Item .env.example .env          # then edit values
scripts\dev.cmd up                   # PostgreSQL, Redis, Qdrant; waits until healthy
scripts\dev.cmd api                  # API with auto-reload on http://127.0.0.1:8000
scripts\dev.cmd check                # lint + type check + tests (what CI runs)
```

`scripts\dev.cmd help` lists every command ([details](docs/development/README.md#developer-commands-task-0108)). The equivalent raw commands are `docker compose -f infra/compose/docker-compose.yml --env-file .env up -d --wait`, then `uv sync` and `uv run uvicorn app.main:create_app --factory --reload --port 8000` in `backend/`. Database migrations (`alembic upgrade head`) arrive in PHASE-02.

Then open http://127.0.0.1:8000/api/v1/docs, or check http://127.0.0.1:8000/api/v1/health/ready (200 when PostgreSQL, Redis and Qdrant are reachable, 503 otherwise).
On Windows **without** `--reload`, add `--loop asyncio:SelectorEventLoop` (psycopg's async mode does not support Windows' default event loop). See [docs/development](docs/development/README.md#running-the-api-task-0106).

Local infrastructure ports (localhost only, chosen to avoid clashing with other projects): PostgreSQL **15432**, Redis **16379**, Qdrant **16333** (HTTP) / **16334** (gRPC). Details: [docs/development](docs/development/README.md#local-infrastructure-task-0104).

## Environment variables
All configuration is via `CORTEX_*` environment variables, documented in `.env.example` *(created in TASK-01.03)*. Never commit `.env`.

## Database & migrations *(planned)*
```powershell
cd backend
uv run alembic upgrade head                        # apply
uv run alembic revision --autogenerate -m "msg"    # create, then review by hand
```

## Running tests *(planned)*
```powershell
cd backend
uv run pytest                    # unit + integration (needs compose services)
uv run pytest -m eval            # retrieval evaluation (slow)
cd ../frontend
npm test
```

## Deployment overview
AWS with S3, RDS PostgreSQL and either a lean single-EC2 Docker Compose deployment or ECS Fargate; HTTPS, secrets in SSM. Nothing is deployed without an approved cost estimate. See [ARCHITECTURE.md §12](ARCHITECTURE.md) and `docs/deployment/` *(Phase 16)*.

## Project management
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — phases, tasks, acceptance criteria, schedule, risks
- [TASK_TRACKER.md](TASK_TRACKER.md) — live status
- [SESSION_LOG.md](SESSION_LOG.md) — per-session handoffs
- [DEVELOPMENT_GUIDELINES.md](DEVELOPMENT_GUIDELINES.md) — how we work
- [CHANGELOG.md](CHANGELOG.md)

# Cortex — Technology Stack

> Exact versions are pinned at scaffold time in `backend/uv.lock`, `frontend/package-lock.json` and image tags in compose files. This file explains **why** each piece exists. Anything not listed here needs an ADR before it is added.

## Backend
| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Required; mature AI ecosystem. Installed via uv. |
| Env / deps | uv + `pyproject.toml` + `uv.lock` | Reproducible, fast (ADR-003). |
| Web framework | FastAPI | Async, Pydantic-native, OpenAPI out of the box. |
| Validation / settings | Pydantic v2, pydantic-settings | Schemas + typed env config. |
| Upload parsing | python-multipart (push parser) | FastAPI's own multipart library, used directly so limits apply while the body streams in (ADR-023). |
| ORM / migrations | SQLAlchemy 2.1 (async; 2.0-style API) + Alembic | Typed ORM, explicit transactions, reviewed migrations. |
| DB driver | psycopg 3 | One driver for async (API) and sync (Alembic/tools). |
| Auth | PyJWT, pwdlib[argon2] | Minimal, well-understood primitives. |
| Background jobs | Celery 5 + Redis | Required; retries, beat scheduling. |
| Object storage | boto3 (S3), local filesystem adapter | Prod S3, dev local. |
| Logging | structlog | JSON logs, context binding, redaction processor. |
| Metrics | prometheus-client | Direct, no framework magic. |
| HTTP client | httpx | Async calls to LLM/embedding APIs; also the test client. |

## AI / retrieval
| Concern | Choice | Why |
|---|---|---|
| Vector DB | Qdrant (Docker; pinned ≥ 1.12) | Dense + sparse named vectors, IDF modifier, payload indexes, tenant indexing. |
| Qdrant client | qdrant-client | Official. |
| Local embeddings / sparse / rerank | FastEmbed (ONNX Runtime) | No PyTorch; CPU; offline tests (ADR-012). |
| Dense model (default) | `BAAI/bge-small-en-v1.5` (384-d) | Good quality/size ratio for CPU. |
| Sparse model | `Qdrant/bm25` | Classic BM25 term weighting; IDF in Qdrant. |
| Reranker | `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder | Small, fast cross-encoder. |
| LLM / remote embeddings | `openai` SDK pointed at any OpenAI-compatible `base_url` | Provider-agnostic. |
| Tokenizer (budgets) | tiktoken | Fast token counting for chunk sizing and context budgets. |
| Parsing | pypdf (with its `crypto` extra); DOCX with the standard library (`zipfile` + streaming XML), not python-docx; charset-normalizer | Permissive licences (ADR-009). `crypto`: AES PDFs with an empty password. DOCX streaming keeps memory flat and caps real decompressed bytes. |
| RAG framework | **none** (no LangChain/LlamaIndex) | Requirement: implement and understand the retrieval flow. |

## Data & infrastructure
| Concern | Choice |
|---|---|
| Relational DB | PostgreSQL 17 (Docker locally, RDS in AWS) |
| Broker / cache / rate limits | Redis 7 |
| Containers | Docker, Docker Compose (dev, test, monitoring profiles) |
| Reverse proxy / TLS (lean prod) | Caddy (automatic HTTPS) — decided in Phase 16 |
| Cloud | AWS: S3, RDS, EC2 or ECS, SSM Parameter Store, CloudFront |
| CI/CD | GitHub Actions |
| Monitoring | Prometheus + Grafana (compose profile) |

## Frontend
| Concern | Choice |
|---|---|
| Framework | React + TypeScript + Vite |
| Styling / components | Tailwind CSS + shadcn/ui |
| State | Zustand (client state); server data fetched through service modules |
| Routing | React Router |
| API types | openapi-typescript generated from backend OpenAPI |
| Charts | Recharts (used by shadcn chart components) |
| Tests | Vitest + Testing Library; Playwright (stretch) |

## Quality tooling
| Concern | Choice |
|---|---|
| Lint/format (Py) | ruff (lint + format) |
| Types (Py) | mypy (strict on `app/`) |
| Tests (Py) | pytest, pytest-asyncio, httpx, respx (HTTP mocks), moto (S3), pytest-cov, pytest-randomly; celery-types (type stubs for Celery, dev only); pyyaml + types-pyyaml (compose configuration tests, dev only) |
| Load tests | Locust |
| Security | pip-audit, npm audit, gitleaks |
| Lint (TS) | ESLint + Prettier, `tsc --noEmit` |

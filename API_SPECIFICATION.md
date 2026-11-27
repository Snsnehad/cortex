# Cortex — API Specification (v1)

> Status: **PROPOSAL — awaiting approval** (TASK-00.05). The generated OpenAPI document (`/api/v1/openapi.json`) becomes the authoritative contract once implemented; this file records intent and any requirement not obvious from the schema.

## 1. General conventions

* Base path: `/api/v1`. JSON (`application/json`) unless stated. Field names `snake_case`. Timestamps ISO-8601 UTC.
* Auth: `Authorization: Bearer <access_token>` on every endpoint except register/login/refresh/health.
* Tenant scope: org-owned resources live under `/orgs/{org_id}/…`. The server verifies the caller's membership in `org_id`; a non-member receives **404** for every org-scoped route (existence is not disclosed).
* **Success responses**: a single resource is returned as the object itself; lists are returned as
  ```json
  { "items": [ ... ], "next_cursor": "eyJjIjoi...", "has_more": true }
  ```
  Query params: `limit` (1–100, default 20), `cursor` (opaque). Keyset pagination — stable under concurrent inserts.
* **Error responses** (all non-2xx):
  ```json
  { "error": { "code": "DOCUMENT_DUPLICATE", "message": "This file already exists in the knowledge base.",
               "details": { "existing_document_id": "..." }, "request_id": "01J..." } }
  ```
  Validation errors use `code: "VALIDATION_ERROR"` with `details.fields = [{loc, msg}]`. Internal errors never leak stack traces or SQL.
* Every response carries `X-Request-ID` (echoed if the client sent a valid one, otherwise generated).
* Rate limits return `429` with `Retry-After`.

### Status codes used
`200` OK · `201` Created · `202` Accepted (async processing started) · `204` No Content · `400` malformed · `401` unauthenticated/expired token · `403` authenticated member lacking role · `404` not found or not visible to caller · `409` conflict (duplicate, last owner) · `413` file too large · `415` unsupported file type · `422` validation · `429` rate limited · `503` dependency unavailable (readiness, LLM down).

## 2. Roles

| Capability | MEMBER | ADMIN | OWNER |
|---|:-:|:-:|:-:|
| View org, KBs, documents | ✓ | ✓ | ✓ |
| Chat (own conversations) | ✓ | ✓ | ✓ |
| Upload / reprocess documents | ✓ | ✓ | ✓ |
| Delete documents | own uploads | ✓ | ✓ |
| Create / update / delete KBs | | ✓ | ✓ |
| View org usage analytics | | ✓ | ✓ |
| Debug search endpoint | | ✓ | ✓ |
| Add/remove MEMBER/ADMIN | | ✓ (not owners) | ✓ |
| Change roles incl. OWNER, update/delete org | | | ✓ |

The "member may upload" default is a product decision recorded in DECISIONS.md (ADR-010) and is a single config point if you prefer ADMIN-only uploads.

## 3. Endpoints

### Health (no auth)
| Method | Path | Description |
|---|---|---|
| GET | `/health/live` | Process is up. Always 200 if serving. |
| GET | `/health/ready` | Checks PostgreSQL, Redis, Qdrant. 200 or 503 with per-dependency status. |

### Authentication
| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| POST | `/auth/register` | `{email, password, full_name, organization_name?}` | 201 `{user, organization, access_token, token_type, expires_in}` + refresh cookie | Creates user + organization + OWNER membership in one transaction (default org name "<full_name>'s workspace"; slug made unique with a 6-hex suffix). Password policy (ADR-021) → 422 `PASSWORD_POLICY`. Duplicate email (any case, race-safe) → 409 `EMAIL_TAKEN`. Rate limited per IP (TASK-03.07). |
| POST | `/auth/login` | `{email, password}` | 200 `{access_token, token_type, expires_in}` + refresh cookie | Unknown email, wrong password and deactivated account all → 401 `INVALID_CREDENTIALS` with identical message and timing. Upgrades outdated password hashes. Rate limited (TASK-03.07). |
| POST | `/auth/refresh` | — (cookie) | 200 `{access_token, token_type, expires_in}` + rotated cookie | Requires header `X-Requested-With: cortex`, otherwise 403 `CSRF_CHECK_FAILED`. No/unknown/reused token → 401 `INVALID_REFRESH_TOKEN` (reuse revokes the whole family, ADR-022); expired → 401 `REFRESH_TOKEN_EXPIRED`. |
| POST | `/auth/logout` | — (cookie) | 204 | Revokes the token family and clears the cookie. Idempotent; works without a cookie. |
| GET | `/auth/me` | — | 200 `{user, memberships: [{organization, role}]}` | 401 `UNAUTHENTICATED` (no token), `TOKEN_EXPIRED`, or `INVALID_TOKEN` (bad token, or user deleted/deactivated since issue). |
| PATCH | `/auth/me` | `{full_name}` | 200 `{user, memberships}` | Account settings. |
| POST | `/auth/me/password` | `{current_password, new_password}` | 204 | Wrong current password → 422 `INVALID_CURRENT_PASSWORD` (field `current_password`); new password checked against the policy. Revokes all *other* sessions; the session sending its refresh cookie stays signed in. |

### Organizations & members
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/orgs` | any | Organizations the caller belongs to. |
| POST | `/orgs` | any | Create organization (caller becomes OWNER). |
| GET | `/orgs/{org_id}` | MEMBER | Org details. |
| PATCH | `/orgs/{org_id}` | OWNER | Rename. |
| DELETE | `/orgs/{org_id}` | OWNER | Async delete (stretch — see plan). |
| GET | `/orgs/{org_id}/members` | MEMBER | Paginated member list. |
| POST | `/orgs/{org_id}/members` | ADMIN | `{email, role}` — adds an **existing** user. Email invitations are out of MVP scope. |
| PATCH | `/orgs/{org_id}/members/{user_id}` | ADMIN/OWNER | `{role}`; only OWNER can grant/revoke OWNER. Last OWNER cannot be demoted → 409 `LAST_OWNER`. |
| DELETE | `/orgs/{org_id}/members/{user_id}` | ADMIN/OWNER, or self | Remove / leave. Last OWNER cannot leave. |

### Knowledge bases
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/orgs/{org_id}/knowledge-bases` | ADMIN | `{name, description?, chunk_size_tokens?, chunk_overlap_tokens?}`; embedding profile = current default. 409 on duplicate name. |
| GET | `/orgs/{org_id}/knowledge-bases` | MEMBER | Paginated; includes document counts by status. |
| GET | `/orgs/{org_id}/knowledge-bases/{kb_id}` | MEMBER | Detail + stats. |
| PATCH | `/orgs/{org_id}/knowledge-bases/{kb_id}` | ADMIN | Name/description. Chunking changes apply to new/reprocessed documents only (response says so). |
| DELETE | `/orgs/{org_id}/knowledge-bases/{kb_id}` | ADMIN | 202; soft-delete then async cleanup of documents/vectors/files. |
| POST | `/orgs/{org_id}/knowledge-bases/{kb_id}/reindex` | ADMIN | Stretch: migrate KB to a new embedding profile. |

### Documents
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/orgs/{org_id}/knowledge-bases/{kb_id}/documents` | MEMBER | `multipart/form-data` field `file`. Accepted: PDF, DOCX, TXT, MD (detected by content). Max size configurable (default 25 MB). → **202** `{document, job}`. Errors: 413 `FILE_TOO_LARGE`, 415 `UNSUPPORTED_FILE_TYPE`, 409 `DOCUMENT_DUPLICATE`, 409 `QUOTA_EXCEEDED`. |
| GET | `/orgs/{org_id}/knowledge-bases/{kb_id}/documents` | MEMBER | Paginated; filter `status`; search `q` on filename. |
| GET | `/orgs/{org_id}/documents/{document_id}` | MEMBER | Metadata, status, counts, error. |
| GET | `/orgs/{org_id}/documents/{document_id}/status` | MEMBER | Lightweight poll: `{status, stage, attempts, error_code, updated_at}`. |
| GET | `/orgs/{org_id}/documents/{document_id}/download` | MEMBER | Streams original (local) or 302 to a short-lived presigned S3 URL. `Content-Disposition: attachment`. |
| GET | `/orgs/{org_id}/documents/{document_id}/chunks` | ADMIN | Paginated chunks (debug/inspection). |
| POST | `/orgs/{org_id}/documents/{document_id}/reprocess` | MEMBER | 202; 409 `JOB_ALREADY_ACTIVE` if processing. |
| DELETE | `/orgs/{org_id}/documents/{document_id}` | uploader or ADMIN | 202; hidden immediately, cleaned up asynchronously. |

### Search (debug / evaluation)
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/orgs/{org_id}/search` | ADMIN | `{query, knowledge_base_ids?, mode: dense|sparse|hybrid, rerank: bool, top_k}` → ranked chunks with per-stage scores and latencies. No LLM call. |

### Conversations & chat
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/orgs/{org_id}/conversations` | MEMBER | `{knowledge_base_id, title?}` → 201. |
| GET | `/orgs/{org_id}/conversations` | MEMBER | Caller's own conversations, paginated by `updated_at`; filter `knowledge_base_id`. |
| GET | `/orgs/{org_id}/conversations/{conversation_id}` | owner of conversation | Conversation metadata. |
| PATCH | `/orgs/{org_id}/conversations/{conversation_id}` | owner | Rename. |
| DELETE | `/orgs/{org_id}/conversations/{conversation_id}` | owner | 204, hard delete with messages/citations. |
| GET | `/orgs/{org_id}/conversations/{conversation_id}/messages` | owner | Paginated history (oldest→newest), each assistant message with citations. |
| POST | `/orgs/{org_id}/conversations/{conversation_id}/messages` | owner | Ask: `{content, options?: {top_k?, rerank?}}`. Default → 201 `{user_message, assistant_message{content, citations[], latency, usage}}`. With `Accept: text/event-stream` → SSE stream (stretch, TASK-11.03): events `retrieval`, `token`, `citations`, `done`, `error`. Rate limited per user. |

Citation object:
```json
{ "marker": 1, "document_id": "…", "document_filename": "handbook.pdf",
  "page_start": 12, "page_end": 13, "section_path": "Benefits > Leave",
  "snippet": "Employees accrue 1.5 days…", "relevance_score": 0.91 }
```

### Analytics (ADMIN)
| Method | Path | Description |
|---|---|---|
| GET | `/orgs/{org_id}/analytics/usage?from&to&granularity=day` | Tokens and estimated cost by day × event_type × model. |
| GET | `/orgs/{org_id}/analytics/documents?from&to` | Docs by status, processing time p50/p95, failure codes, pages/chunks processed. |
| GET | `/orgs/{org_id}/analytics/retrieval?from&to` | Retrieval / rerank / generation / total latency p50/p95/p99, questions per day, "not found" answer rate. |

### Operational (non-API)
* `GET /metrics` — Prometheus exposition; bound to the internal network / protected in production, not routed publicly.
* `GET /api/v1/docs`, `/api/v1/openapi.json` — enabled in dev/staging; disabled or auth-protected in production (decision in TASK-15.04).

## 4. Rate limits (initial values, configurable)

| Scope | Limit |
|---|---|
| `POST /auth/login` | 10 / min per IP, 5 / min per email |
| `POST /auth/register` | 5 / hour per IP |
| Upload | 30 / hour per user |
| Ask question | 20 / min per user |
| Everything else authenticated | 300 / min per user |

Implemented with a Redis sliding-window counter (ADR-011). If Redis is unavailable the limiter **fails open for reads and fails closed for auth endpoints** (logged + metric).

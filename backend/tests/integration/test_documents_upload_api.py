"""POST /orgs/{org_id}/knowledge-bases/{kb_id}/documents (TASK-04.05).

Cross-tenant probes for this route are in the isolation framework (tenancy.py). Validation
details are unit-tested in tests/unit/test_upload_*.py; here each refusal is checked once
through HTTP, together with what it leaves behind (nothing).
"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import Document, JobStatus, ProcessingJob
from app.repositories.documents import DocumentRepository
from app.storage.local import LocalStorage
from tests.integration.api_helpers import Api, RecordingJobQueue, code, team
from tests.integration.factories import create_kb
from tests.unit.test_upload_detect import make_docx

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
TEXT = b"# Handbook\n\nVacation policy: 25 days.\n"
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


@pytest.fixture
async def world(session_factory: Factory) -> dict[str, Any]:
    t = await team(session_factory)
    t["kb"] = await create_kb(session_factory, t["org"], name="Handbook")
    return t


def docs_path(t: dict[str, Any], kb_id: Any = None) -> str:
    return f"/api/v1/orgs/{t['org'].id}/knowledge-bases/{kb_id or t['kb'].id}/documents"


def headers(app: FastAPI, user: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.access_tokens.issue(user.id).token}"}


async def upload(
    app: FastAPI,
    client: httpx.AsyncClient,
    t: dict[str, Any],
    content: bytes = TEXT,
    filename: str = "handbook.md",
    *,
    who: str = "member",
    kb_id: Any = None,
) -> httpx.Response:
    return await client.post(
        docs_path(t, kb_id),
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers(app, t[who]),
    )


async def counts(factory: Factory) -> tuple[int, int]:
    async with factory() as session:
        documents = await session.scalar(select(func.count()).select_from(Document))
        jobs = await session.scalar(select(func.count()).select_from(ProcessingJob))
    return int(documents or 0), int(jobs or 0)


def stored_files(app: FastAPI) -> list[Path]:
    storage: LocalStorage = app.state.storage
    if not storage.root.exists():
        return []
    return [p for p in storage.root.rglob("*") if p.is_file()]


# --- success --------------------------------------------------------------------------------


async def test_upload_returns_202_with_a_pending_document_and_a_queued_job(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory, world: Any
) -> None:
    response = await upload(api_app, api_client, world)

    assert response.status_code == 202, response.text
    body = response.json()
    document, job = body["document"], body["job"]
    assert document["filename"] == "handbook.md"
    assert document["mime_type"] == "text/markdown"  # detected, not the declared octet-stream
    assert document["file_size_bytes"] == len(TEXT)
    assert document["status"] == "PENDING"
    assert document["knowledge_base_id"] == str(world["kb"].id)
    assert document["uploaded_by_user_id"] == str(world["member"].id)
    assert "storage_key" not in document
    assert job["document_id"] == document["id"]
    assert (job["job_type"], job["status"], job["attempts"], job["max_attempts"]) == (
        "INGEST",
        "QUEUED",
        0,
        5,
    )
    async with session_factory() as session:
        row = await session.get(Document, document["id"])
        assert row is not None
        stored = api_app.state.storage.path_for(row.storage_key).read_bytes()
    assert stored == TEXT
    assert await counts(session_factory) == (1, 1)


@pytest.mark.parametrize(
    ("content", "filename", "mime"),
    [
        (PDF, "scan.pdf", "application/pdf"),
        (
            make_docx(),
            "minutes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (TEXT, "notes.txt", "text/plain"),
    ],
)
async def test_each_supported_type(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    world: Any,
    content: bytes,
    filename: str,
    mime: str,
) -> None:
    response = await upload(api_app, api_client, world, content, filename)
    assert response.status_code == 202, response.text
    assert response.json()["document"]["mime_type"] == mime


@pytest.mark.parametrize("who", ["owner", "admin", "member"])
async def test_every_member_may_upload(
    api_app: FastAPI, api_client: httpx.AsyncClient, world: Any, who: str
) -> None:
    response = await upload(api_app, api_client, world, who=who)
    assert response.status_code == 202


async def test_the_kb_stats_show_the_new_document(
    api: Api, api_app: FastAPI, api_client: httpx.AsyncClient, world: Any
) -> None:
    await upload(api_app, api_client, world)

    kb = await api(
        world["member"], "GET", f"/orgs/{world['org'].id}/knowledge-bases/{world['kb'].id}"
    )

    assert kb.json()["stats"]["documents_by_status"]["PENDING"] == 1
    assert kb.json()["stats"]["total_bytes"] == len(TEXT)


# --- refusals leave nothing behind ----------------------------------------------------------


async def test_duplicate_is_409_with_the_existing_id(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory, world: Any
) -> None:
    first = (await upload(api_app, api_client, world)).json()["document"]

    again = await upload(api_app, api_client, world, filename="renamed copy.md", who="admin")

    assert code(again) == (409, "DOCUMENT_DUPLICATE")
    assert again.json()["error"]["details"] == {"existing_document_id": first["id"]}
    assert await counts(session_factory) == (1, 1)
    assert len(stored_files(api_app)) == 1


@pytest.mark.parametrize(
    ("content", "filename", "expected"),
    [
        (b"MZ\x90\x00" + b"\x00" * 64, "invoice.pdf", (415, "UNSUPPORTED_FILE_TYPE")),
        (b"", "empty.txt", (422, "FILE_EMPTY")),
    ],
)
async def test_rejected_files_leave_no_trace(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    world: Any,
    content: bytes,
    filename: str,
    expected: tuple[int, str],
) -> None:
    response = await upload(api_app, api_client, world, content, filename)

    assert code(response) == expected
    assert await counts(session_factory) == (0, 0)
    assert stored_files(api_app) == []


async def test_too_large_is_413(
    test_settings: Settings, session_factory: Factory, world: Any, db_engine: AsyncEngine
) -> None:
    settings = test_settings.model_copy(
        update={"storage": test_settings.storage.model_copy(update={"max_upload_bytes": 100})}
    )
    async with app_client(settings) as (app, client):
        response = await upload(app, client, world, b"x" * 101, "big.txt")

    assert code(response) == (413, "FILE_TOO_LARGE")
    assert await counts(session_factory) == (0, 0)


async def test_not_multipart_is_400(
    api_app: FastAPI, api_client: httpx.AsyncClient, world: Any
) -> None:
    response = await api_client.post(
        docs_path(world), json={"file": "nope"}, headers=headers(api_app, world["member"])
    )
    assert code(response) == (400, "INVALID_UPLOAD")


class CountingBody:
    def __init__(self) -> None:
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(100):
            self.pulled += 1
            yield b"x" * 65536


@pytest.mark.parametrize("which", ["unknown", "deleted"])
async def test_missing_kb_is_404_before_the_body_is_read(
    api: Api,
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    world: Any,
    which: str,
) -> None:
    kb_id = "01900000-0000-7000-8000-000000000000"
    if which == "deleted":
        kb_id = str(world["kb"].id)
        await api(world["owner"], "DELETE", f"/orgs/{world['org'].id}/knowledge-bases/{kb_id}")
    body = CountingBody()

    response = await api_client.post(
        docs_path(world, kb_id),
        content=body,
        headers={
            **headers(api_app, world["member"]),
            "Content-Type": "multipart/form-data; boundary=x",
        },
    )

    assert code(response) == (404, "KNOWLEDGE_BASE_NOT_FOUND")
    assert body.pulled == 0


async def test_document_and_job_are_one_transaction(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    world: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the job row cannot be written, the document row must not exist either, and the
    stored file is removed: a worker never finds a document without a job (or vice versa)."""
    original = DocumentRepository.add_job

    def add_broken_job(self: DocumentRepository, job: ProcessingJob) -> None:
        job.max_attempts = -1  # violates CHECK attempts_valid at commit
        original(self, job)

    monkeypatch.setattr(DocumentRepository, "add_job", add_broken_job)

    response = await upload(api_app, api_client, world)

    assert response.status_code == 500
    assert await counts(session_factory) == (0, 0)
    assert stored_files(api_app) == []


# --- rate limit -----------------------------------------------------------------------------


class _AppClient:
    def __init__(self, settings: Settings) -> None:
        self.app = create_app(settings)
        self.app.state.job_queue.close()
        self.app.state.job_queue = RecordingJobQueue()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def __aenter__(self) -> tuple[FastAPI, httpx.AsyncClient]:
        return self.app, self.client

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()
        await self.app.state.db_engine.dispose()
        await self.app.state.redis.aclose()


app_client: Callable[[Settings], _AppClient] = _AppClient


async def test_uploads_are_rate_limited_per_user(
    test_settings: Settings, world: Any, db_engine: AsyncEngine
) -> None:
    settings = test_settings.model_copy(
        update={
            "rate_limit": test_settings.rate_limit.model_copy(
                update={"upload_per_user_per_hour": 2}
            )
        }
    )
    async with app_client(settings) as (app, client):
        statuses = [
            (await upload(app, client, world, TEXT + str(i).encode(), f"f{i}.md")).status_code
            for i in range(3)
        ]
        other_user = await upload(app, client, world, TEXT + b"other", who="admin")

    assert statuses == [202, 202, 429]
    assert other_user.status_code == 202


async def test_upload_limit_fails_open_when_redis_is_down(
    test_settings: Settings, world: Any, db_engine: AsyncEngine
) -> None:
    settings = test_settings.model_copy(
        update={
            "redis": test_settings.redis.model_copy(
                update={"url": SecretStr("redis://127.0.0.1:1/0")}
            )
        }
    )
    async with app_client(settings) as (app, client):
        response = await upload(app, client, world)

    assert response.status_code == 202


async def test_openapi_documents_the_multipart_body(api_app: FastAPI) -> None:
    operation = api_app.openapi()["paths"][
        "/api/v1/orgs/{org_id}/knowledge-bases/{kb_id}/documents"
    ]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"]["format"] == "binary"


# --- enqueueing (TASK-05.04) ----------------------------------------------------------------


async def test_upload_enqueues_the_job_after_it_is_saved(
    api_app: FastAPI, api_client: httpx.AsyncClient, world: Any
) -> None:
    response = await upload(api_app, api_client, world)

    assert response.status_code == 202
    job_id = response.json()["job"]["id"]
    assert [str(j) for j in api_app.state.job_queue.enqueued] == [job_id]


async def test_upload_succeeds_even_if_the_broker_is_down(
    api_app: FastAPI, api_client: httpx.AsyncClient, session_factory: Factory, world: Any
) -> None:
    api_app.state.job_queue = RecordingJobQueue(available=False)

    response = await upload(api_app, api_client, world)

    assert response.status_code == 202  # the reconciler will enqueue the QUEUED job later
    async with session_factory() as session:
        job = await session.get(ProcessingJob, response.json()["job"]["id"])
    assert job is not None
    assert job.status is JobStatus.QUEUED


async def test_refused_uploads_enqueue_nothing(
    api_app: FastAPI, api_client: httpx.AsyncClient, world: Any
) -> None:
    await upload(api_app, api_client, world)  # the original: enqueued once
    await upload(api_app, api_client, world, filename="copy.md")  # duplicate → 409
    await upload(api_app, api_client, world, b"", "empty.txt")  # empty → 422

    assert len(api_app.state.job_queue.enqueued) == 1

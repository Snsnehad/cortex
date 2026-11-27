"""Document reads: list (filter, search, pagination), get, status, download (TASK-04.06).

Cross-tenant probes for these routes are in the isolation framework (tenancy.py).
"""

import base64
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentStatus, JobStatus, JobType, KnowledgeBase, ProcessingJob
from app.storage.local import LocalStorage
from tests.integration.api_helpers import code, team
from tests.integration.db_helpers import add_all_and_commit
from tests.integration.factories import create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def world(session_factory: Factory) -> dict[str, Any]:
    t = await team(session_factory)
    t["kb"] = await create_kb(session_factory, t["org"], name="Handbook")
    return t


class Client:
    def __init__(self, app: FastAPI, http: httpx.AsyncClient, t: dict[str, Any]) -> None:
        self.app, self.http, self.t = app, http, t
        self.org = f"/api/v1/orgs/{t['org'].id}"

    def auth(self, who: str = "member") -> dict[str, str]:
        token = self.app.state.access_tokens.issue(self.t[who].id).token
        return {"Authorization": f"Bearer {token}"}

    async def upload(self, content: bytes, filename: str, who: str = "member") -> dict[str, Any]:
        response = await self.http.post(
            f"{self.org}/knowledge-bases/{self.t['kb'].id}/documents",
            files={"file": (filename, content, "text/plain")},
            headers=self.auth(who),
        )
        assert response.status_code == 202, response.text
        document: dict[str, Any] = response.json()["document"]
        return document

    async def get(self, path: str, who: str = "member", **params: Any) -> httpx.Response:
        return await self.http.get(f"{self.org}{path}", params=params, headers=self.auth(who))

    async def list(self, who: str = "member", **params: Any) -> httpx.Response:
        return await self.get(f"/knowledge-bases/{self.t['kb'].id}/documents", who, **params)


@pytest.fixture
def client(api_app: FastAPI, api_client: httpx.AsyncClient, world: dict[str, Any]) -> Client:
    return Client(api_app, api_client, world)


async def set_status(factory: Factory, document_id: str, status: DocumentStatus) -> None:
    async with factory() as session:
        await session.execute(
            update(Document).where(Document.id == document_id).values(status=status)
        )
        await session.commit()


def names(response: httpx.Response) -> list[str]:
    return [item["filename"] for item in response.json()["items"]]


# --- list -----------------------------------------------------------------------------------


async def test_list_is_newest_first_and_paginated(client: Client) -> None:
    for i in range(5):
        await client.upload(f"document {i}\n".encode(), f"doc{i}.txt")

    seen: list[str] = []
    cursor = None
    while True:
        page = await client.list(limit=2, **({"cursor": cursor} if cursor else {}))
        seen += names(page)
        cursor = page.json()["next_cursor"]
        if not page.json()["has_more"]:
            break

    assert seen == [f"doc{i}.txt" for i in reversed(range(5))]


async def test_documents_being_deleted_are_hidden_everywhere(
    client: Client, session_factory: Factory
) -> None:
    keep = await client.upload(b"keep\n", "keep.txt")
    gone = await client.upload(b"gone\n", "gone.txt")
    await set_status(session_factory, gone["id"], DocumentStatus.DELETING)

    assert names(await client.list()) == ["keep.txt"]
    for suffix in ("", "/status", "/download"):
        response = await client.get(f"/documents/{gone['id']}{suffix}")
        assert code(response) == (404, "DOCUMENT_NOT_FOUND")
    assert (await client.get(f"/documents/{keep['id']}")).status_code == 200


async def test_documents_of_a_deleted_kb_are_gone(
    client: Client, session_factory: Factory, world: dict[str, Any]
) -> None:
    document = await client.upload(b"text\n", "a.txt")
    async with session_factory() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == world["kb"].id)
            .values(deleted_at=func.now())
        )
        await session.commit()

    assert code(await client.list()) == (404, "KNOWLEDGE_BASE_NOT_FOUND")
    for suffix in ("", "/status", "/download"):
        response = await client.get(f"/documents/{document['id']}{suffix}")
        assert code(response) == (404, "DOCUMENT_NOT_FOUND")


async def test_status_filter(client: Client, session_factory: Factory) -> None:
    ready = await client.upload(b"ready\n", "ready.txt")
    await client.upload(b"pending\n", "pending.txt")
    await set_status(session_factory, ready["id"], DocumentStatus.READY)

    assert names(await client.list(status="READY")) == ["ready.txt"]
    assert names(await client.list(status="PENDING")) == ["pending.txt"]
    assert names(await client.list(status="FAILED")) == []
    assert (await client.list(status="DELETING")).status_code == 422  # not a filter value


FILENAMES = ["Q3 report.txt", "report_final.txt", "50% off.txt", "plain.txt"]


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("REPORT", ["report_final.txt", "Q3 report.txt"]),  # newest first
        ("_final", ["report_final.txt"]),  # "_" is literal, not "any character"
        ("50%", ["50% off.txt"]),  # "%" is literal, not "anything"
        ("%", ["50% off.txt"]),
        # The LIKE escape character itself: matches nothing (stored names never contain a
        # backslash: sanitization treats it as a path separator) and must not error.
        ("\\", []),
        ("nothing", []),
        ("   ", list(reversed(FILENAMES))),  # blank search = no search
    ],
)
async def test_filename_search_is_case_insensitive_and_literal(
    client: Client, q: str, expected: list[str]
) -> None:
    for i, filename in enumerate(FILENAMES):
        await client.upload(f"content {i}.".encode(), filename)

    response = await client.list(q=q)

    assert response.status_code == 200
    assert names(response) == expected


async def test_search_text_too_long_is_422(client: Client) -> None:
    assert (await client.list(q="x" * 201)).status_code == 422


async def test_cursor_is_bound_to_the_filters_and_hides_the_search(client: Client) -> None:
    for i in range(3):
        await client.upload(f"secret project {i}\n".encode(), f"secret-project-{i}.txt")

    first = (await client.list(limit=1, q="secret-project")).json()
    cursor = first["next_cursor"]
    payload = base64.urlsafe_b64decode(cursor.split(".")[0] + "==")

    assert b"secret" not in payload  # cursors are readable: the search text is only hashed
    assert json.loads(payload)  # (it is a plain JSON payload)
    assert code(await client.list(limit=1, cursor=cursor, q="other")) == (400, "INVALID_CURSOR")
    assert code(await client.list(limit=1, cursor=cursor)) == (400, "INVALID_CURSOR")
    assert (await client.list(limit=1, cursor=cursor, q="secret-project")).status_code == 200


# --- get and status -------------------------------------------------------------------------


async def test_get_returns_metadata_and_the_latest_job(client: Client) -> None:
    uploaded = await client.upload(b"# Notes\n", "notes.md")

    response = await client.get(f"/documents/{uploaded['id']}")

    body = response.json()
    assert response.status_code == 200
    assert {k: body[k] for k in ("id", "filename", "mime_type", "status")} == {
        "id": uploaded["id"],
        "filename": "notes.md",
        "mime_type": "text/markdown",
        "status": "PENDING",
    }
    assert body["latest_job"]["status"] == "QUEUED"
    assert "storage_key" not in body


async def test_status_reports_the_newest_job(client: Client, session_factory: Factory) -> None:
    uploaded = await client.upload(b"text\n", "a.txt")
    async with session_factory() as session:
        document = await session.get(Document, uploaded["id"])
        assert document is not None
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.document_id == document.id)
            .values(status=JobStatus.FAILED, error_code="EXTRACTION_FAILED", attempts=5)
        )
        await session.commit()
        retry = ProcessingJob(
            organization_id=document.organization_id,
            document_id=document.id,
            job_type=JobType.INGEST,
            status=JobStatus.RUNNING,
            attempts=1,
        )
    await add_all_and_commit(session_factory, retry)

    body = (await client.get(f"/documents/{uploaded['id']}/status")).json()

    assert body["job_status"] == "RUNNING"
    assert body["attempts"] == 1
    assert body["error_code"] is None  # the old job's error is history


async def test_unknown_document_is_404(client: Client) -> None:
    for suffix in ("", "/status", "/download"):
        response = await client.get(f"/documents/01900000-0000-7000-8000-000000000000{suffix}")
        assert code(response) == (404, "DOCUMENT_NOT_FOUND")


# --- download -------------------------------------------------------------------------------


async def test_local_download_streams_an_attachment(client: Client) -> None:
    content = b"<html><script>alert('stored xss?')</script></html>\n"
    uploaded = await client.upload(content, "page.html")  # valid UTF-8 text → text/plain

    response = await client.get(f"/documents/{uploaded['id']}/download")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith('attachment; filename="page.html"')
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(content))


async def test_missing_file_fails_cleanly_before_the_response_starts(
    client: Client, api_app: FastAPI, session_factory: Factory
) -> None:
    uploaded = await client.upload(b"soon gone\n", "a.txt")
    async with session_factory() as session:
        document = await session.get(Document, uploaded["id"])
        assert document is not None
        storage: LocalStorage = api_app.state.storage
        storage.path_for(document.storage_key).unlink()

    response = await client.get(f"/documents/{uploaded['id']}/download")

    # A clean 500 with the error envelope, not a 200 with a truncated body.
    assert code(response) == (500, "INTERNAL_ERROR")


async def test_s3_download_redirects_to_a_short_lived_link(
    client: Client, api_app: FastAPI, test_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded = await client.upload(b"text\n", "report.txt")
    calls: list[dict[str, Any]] = []

    async def presign(key: str, *, filename: str, expires_in_seconds: int) -> str:
        calls.append({"key": key, "filename": filename, "ttl": expires_in_seconds})
        return "https://bucket.s3.example/signed?X-Amz-Signature=abc"

    monkeypatch.setattr(api_app.state.storage, "presigned_get_url", presign)

    response = await client.get(f"/documents/{uploaded['id']}/download")

    assert response.status_code == 302
    assert response.headers["location"] == "https://bucket.s3.example/signed?X-Amz-Signature=abc"
    assert response.headers["cache-control"] == "private, no-store"
    assert calls == [
        {
            "key": calls[0]["key"],
            "filename": "report.txt",
            "ttl": test_settings.storage.presigned_url_ttl_seconds,
        }
    ]
    assert calls[0]["key"].endswith(f"/docs/{uploaded['id']}/original")


@pytest.mark.parametrize("who", ["owner", "admin", "member"])
async def test_every_member_may_read(client: Client, who: str) -> None:
    uploaded = await client.upload(b"text\n", "a.txt", who="owner")
    for path in ("", "/status", "/download"):
        assert (await client.get(f"/documents/{uploaded['id']}{path}", who)).status_code == 200
    assert (await client.list(who)).status_code == 200

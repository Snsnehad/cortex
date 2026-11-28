"""Reprocess and delete through the API, and the knowledge-base cascade (TASK-05.05)."""

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    ACTIVE_JOB_STATUSES,
    Document,
    DocumentStatus,
    JobStatus,
    JobType,
    ProcessingJob,
)
from tests.integration.api_helpers import code, team
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

    def auth(self, who: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.app.state.access_tokens.issue(self.t[who].id).token}"
        }

    async def upload(self, content: bytes = b"text\n", who: str = "member") -> dict[str, Any]:
        response = await self.http.post(
            f"{self.org}/knowledge-bases/{self.t['kb'].id}/documents",
            files={"file": ("a.txt", content, "text/plain")},
            headers=self.auth(who),
        )
        assert response.status_code == 202, response.text
        body: dict[str, Any] = response.json()
        return body

    async def reprocess(self, document_id: str, who: str = "member") -> httpx.Response:
        return await self.http.post(
            f"{self.org}/documents/{document_id}/reprocess", headers=self.auth(who)
        )

    async def delete(self, document_id: str, who: str = "member") -> httpx.Response:
        return await self.http.delete(f"{self.org}/documents/{document_id}", headers=self.auth(who))


@pytest.fixture
def client(api_app: FastAPI, api_client: httpx.AsyncClient, world: dict[str, Any]) -> Client:
    return Client(api_app, api_client, world)


async def jobs_of(factory: Factory, document_id: str) -> list[ProcessingJob]:
    async with factory() as session:
        rows = await session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.created_at, ProcessingJob.id)
        )
        return list(rows.all())


async def finish_jobs(factory: Factory, document_id: str, status: JobStatus) -> None:
    async with factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
            .values(status=status)
        )
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.FAILED, error_code="PROCESSING_NOT_AVAILABLE")
        )
        await session.commit()


# --- reprocess ------------------------------------------------------------------------------


async def test_reprocess_queues_a_new_job_and_resets_the_document(
    client: Client, session_factory: Factory, api_app: FastAPI
) -> None:
    uploaded = await client.upload()
    document_id = uploaded["document"]["id"]
    await finish_jobs(session_factory, document_id, JobStatus.FAILED)

    response = await client.reprocess(document_id)

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["document"]["status"], body["document"]["error_code"]) == ("PENDING", None)
    assert (body["job"]["job_type"], body["job"]["status"]) == ("INGEST", "QUEUED")
    assert [str(j) for j in api_app.state.job_queue.enqueued] == [
        uploaded["job"]["id"],
        body["job"]["id"],
    ]


async def test_reprocess_while_processing_is_409(client: Client) -> None:
    uploaded = await client.upload()  # its job is still QUEUED (active)

    response = await client.reprocess(uploaded["document"]["id"])

    assert code(response) == (409, "JOB_ALREADY_ACTIVE")


async def test_two_reprocess_requests_at_once_make_one_job(
    client: Client, session_factory: Factory
) -> None:
    document_id = (await client.upload())["document"]["id"]
    await finish_jobs(session_factory, document_id, JobStatus.SUCCEEDED)

    responses = await asyncio.gather(
        client.reprocess(document_id, "member"), client.reprocess(document_id, "admin")
    )

    assert sorted(r.status_code for r in responses) == [202, 409]
    active = [
        j for j in await jobs_of(session_factory, document_id) if j.status in ACTIVE_JOB_STATUSES
    ]
    assert len(active) == 1


# --- delete ---------------------------------------------------------------------------------


async def test_delete_hides_the_document_cancels_its_job_and_queues_removal(
    client: Client, session_factory: Factory, api_app: FastAPI
) -> None:
    uploaded = await client.upload()
    document_id = uploaded["document"]["id"]

    response = await client.delete(document_id)  # by its uploader, a plain member

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "DELETING"
    assert (body["job"]["job_type"], body["job"]["status"]) == ("DELETE", "QUEUED")
    ingest, delete_job = await jobs_of(session_factory, document_id)
    assert (ingest.status, delete_job.job_type) == (JobStatus.CANCELLED, JobType.DELETE)
    assert str(api_app.state.job_queue.enqueued[-1]) == body["job"]["id"]
    assert code(
        await client.http.get(
            f"{client.org}/documents/{document_id}", headers=client.auth("member")
        )
    ) == (404, "DOCUMENT_NOT_FOUND")
    # The same file can be uploaded again at once (a DELETING copy does not count).
    assert (await client.upload())["document"]["id"] != document_id


async def test_only_the_uploader_or_an_admin_may_delete(
    client: Client, session_factory: Factory
) -> None:
    owner_doc = (await client.upload(b"owner's\n", who="owner"))["document"]["id"]

    refused = await client.delete(owner_doc, who="member")  # someone else's document

    assert code(refused) == (403, "PERMISSION_DENIED")
    assert [j.job_type for j in await jobs_of(session_factory, owner_doc)] == [JobType.INGEST]
    assert (await client.delete(owner_doc, who="admin")).status_code == 202


async def test_a_deleted_document_cannot_be_deleted_or_reprocessed_again(
    client: Client,
) -> None:
    document_id = (await client.upload())["document"]["id"]
    await client.delete(document_id)

    assert code(await client.delete(document_id)) == (404, "DOCUMENT_NOT_FOUND")
    assert code(await client.reprocess(document_id)) == (404, "DOCUMENT_NOT_FOUND")


async def test_delete_and_reprocess_at_once_end_consistent(
    client: Client, session_factory: Factory
) -> None:
    """Both lock the document row, so one runs after the other. Whatever the order, the
    document ends DELETING with exactly one active job: its DELETE job."""
    document_id = (await client.upload())["document"]["id"]
    await finish_jobs(session_factory, document_id, JobStatus.FAILED)

    await asyncio.gather(client.reprocess(document_id), client.delete(document_id, "admin"))

    async with session_factory() as session:
        document = await session.get(Document, document_id)
    assert document is not None
    assert document.status is DocumentStatus.DELETING
    active = [
        j for j in await jobs_of(session_factory, document_id) if j.status in ACTIVE_JOB_STATUSES
    ]
    assert [j.job_type for j in active] == [JobType.DELETE]


# --- knowledge-base cascade -----------------------------------------------------------------


async def test_deleting_a_kb_queues_the_removal_of_its_documents(
    client: Client, session_factory: Factory, api_app: FastAPI, world: dict[str, Any]
) -> None:
    ids = [(await client.upload(f"doc {i}\n".encode()))["document"]["id"] for i in range(3)]
    api_app.state.job_queue.enqueued.clear()

    response = await client.http.delete(
        f"{client.org}/knowledge-bases/{world['kb'].id}", headers=client.auth("admin")
    )

    assert response.status_code == 202
    async with session_factory() as session:
        documents = (await session.scalars(select(Document).where(Document.id.in_(ids)))).all()
    assert {d.status for d in documents} == {DocumentStatus.DELETING}
    for document_id in ids:
        types = {j.job_type: j.status for j in await jobs_of(session_factory, document_id)}
        assert types == {JobType.INGEST: JobStatus.CANCELLED, JobType.DELETE: JobStatus.QUEUED}
    assert len(api_app.state.job_queue.enqueued) == 3

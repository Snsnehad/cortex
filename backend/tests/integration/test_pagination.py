"""Keyset pagination against PostgreSQL and over HTTP (TASK-02.07)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.pagination import CursorCodec, InvalidCursorError
from app.dependencies.pagination import CursorCodecDep, PageParams
from app.main import create_app
from app.models import Document, KnowledgeBase
from app.repositories.pagination import KeysetOrder, PageRequest, fetch_page
from app.schemas.pagination import Page
from tests.integration.db_helpers import add_all_and_commit, new_document, new_kb, new_org

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

Factory = async_sessionmaker[AsyncSession]
codec = CursorCodec(b"integration-test-cursor-secret")
NEWEST_FIRST = KeysetOrder(Document.created_at, Document.id, descending=True)
OLDEST_FIRST = KeysetOrder(Document.created_at, Document.id, descending=False)


def scope_for(kb: KnowledgeBase, order: KeysetOrder) -> str:
    direction = "desc" if order.descending else "asc"
    return f"documents:kb={kb.id}:created_at:{direction}"


async def kb_with_documents(factory: Factory, count: int, *, slug: str = "acme") -> KnowledgeBase:
    """``count`` documents inserted in ONE transaction: they all share the same created_at
    (now() is the transaction start time), the worst case for unstable ordering."""
    org = new_org(slug)
    await add_all_and_commit(factory, org)
    kb = new_kb(org)
    await add_all_and_commit(factory, kb)
    await add_all_and_commit(
        factory, *(new_document(kb, content=f"{slug}-{i}") for i in range(count))
    )
    return kb


async def page_of(
    factory: Factory,
    kb: KnowledgeBase,
    *,
    limit: int,
    cursor: str | None = None,
    order: KeysetOrder = NEWEST_FIRST,
) -> Any:
    async with factory() as session:
        stmt = select(Document).where(
            Document.organization_id == kb.organization_id,
            Document.knowledge_base_id == kb.id,
        )
        return await fetch_page(
            session,
            stmt,
            order,
            page=PageRequest(limit=limit, cursor=cursor),
            scope=scope_for(kb, order),
            codec=codec,
        )


async def all_pages(
    factory: Factory, kb: KnowledgeBase, *, limit: int, order: KeysetOrder = NEWEST_FIRST
) -> list[list[uuid.UUID]]:
    pages: list[list[uuid.UUID]] = []
    cursor: str | None = None
    while True:
        result = await page_of(factory, kb, limit=limit, cursor=cursor, order=order)
        pages.append([doc.id for doc in result.items])
        if not result.has_more:
            assert result.next_cursor is None
            return pages
        cursor = result.next_cursor


async def ids_in_order(factory: Factory, kb: KnowledgeBase, *, descending: bool) -> list[uuid.UUID]:
    async with factory() as session:
        column_order = (
            (Document.created_at.desc(), Document.id.desc())
            if descending
            else (Document.created_at.asc(), Document.id.asc())
        )
        rows = await session.scalars(
            select(Document.id).where(Document.knowledge_base_id == kb.id).order_by(*column_order)
        )
        return list(rows.all())


# --- correctness --------------------------------------------------------------------------


async def test_rows_with_identical_timestamps_are_paged_exactly_once(
    session_factory: Factory,
) -> None:
    kb = await kb_with_documents(session_factory, 45)

    pages = await all_pages(session_factory, kb, limit=10)

    assert [len(page) for page in pages] == [10, 10, 10, 10, 5]
    flattened = [doc_id for page in pages for doc_id in page]
    assert len(set(flattened)) == 45  # no duplicates
    assert flattened == await ids_in_order(session_factory, kb, descending=True)  # no gaps


async def test_ascending_order(session_factory: Factory) -> None:
    kb = await kb_with_documents(session_factory, 7)

    pages = await all_pages(session_factory, kb, limit=3, order=OLDEST_FIRST)

    assert [doc_id for page in pages for doc_id in page] == await ids_in_order(
        session_factory, kb, descending=False
    )


async def test_exact_multiple_of_the_page_size_ends_without_an_empty_page(
    session_factory: Factory,
) -> None:
    kb = await kb_with_documents(session_factory, 20)

    pages = await all_pages(session_factory, kb, limit=10)

    assert [len(page) for page in pages] == [10, 10]


async def test_empty_listing(session_factory: Factory) -> None:
    kb = await kb_with_documents(session_factory, 0)

    result = await page_of(session_factory, kb, limit=10)

    assert (list(result.items), result.next_cursor, result.has_more) == ([], None, False)


async def test_inserts_between_pages_cause_no_duplicates_or_skips(
    session_factory: Factory,
) -> None:
    kb = await kb_with_documents(session_factory, 15)
    original = await ids_in_order(session_factory, kb, descending=True)

    first = await page_of(session_factory, kb, limit=5)
    # New uploads arrive while the user is paging (newest-first: they sort before the cursor).
    await add_all_and_commit(
        session_factory, *(new_document(kb, content=f"late-{i}") for i in range(4))
    )
    second = await page_of(session_factory, kb, limit=5, cursor=first.next_cursor)
    third = await page_of(session_factory, kb, limit=5, cursor=second.next_cursor)

    seen = [d.id for d in first.items] + [d.id for d in second.items] + [d.id for d in third.items]
    assert seen == original  # an OFFSET-based page 2 would repeat 4 rows here
    assert third.has_more is False


async def test_deleting_the_cursor_row_does_not_break_the_next_page(
    session_factory: Factory,
) -> None:
    kb = await kb_with_documents(session_factory, 12)
    original = await ids_in_order(session_factory, kb, descending=True)

    first = await page_of(session_factory, kb, limit=5)
    async with session_factory() as session:
        await session.execute(delete(Document).where(Document.id == first.items[-1].id))
        await session.commit()
    second = await page_of(session_factory, kb, limit=5, cursor=first.next_cursor)

    assert [d.id for d in second.items] == original[5:10]


async def test_cursor_from_another_knowledge_base_is_rejected(session_factory: Factory) -> None:
    kb_a = await kb_with_documents(session_factory, 3, slug="acme")
    kb_b = await kb_with_documents(session_factory, 3, slug="globex")
    first_of_a = await page_of(session_factory, kb_a, limit=1)

    with pytest.raises(InvalidCursorError):
        await page_of(session_factory, kb_b, limit=1, cursor=first_of_a.next_cursor)


# --- HTTP ------------------------------------------------------------------------------------


class DocumentOut(BaseModel):
    # Must be declared in the class: pydantic builds the validator at class creation.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str


@pytest.fixture
async def client(session_factory: Factory) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(Settings(_env_file=None))

    @app.get("/_test/kbs/{kb_id}/documents", response_model=Page[DocumentOut])
    async def list_documents(
        kb_id: uuid.UUID, page: PageParams, codec: CursorCodecDep
    ) -> Page[DocumentOut]:
        async with session_factory() as session:
            stmt = select(Document).where(Document.knowledge_base_id == kb_id)
            result = await fetch_page(
                session,
                stmt,
                NEWEST_FIRST,
                page=page,
                scope=f"documents:kb={kb_id}:created_at:desc",
                codec=codec,
            )
        return Page[DocumentOut].from_result(result, DocumentOut.model_validate)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await app.state.db_engine.dispose()


async def test_http_list_envelope_and_paging(
    client: httpx.AsyncClient, session_factory: Factory
) -> None:
    kb = await kb_with_documents(session_factory, 5)

    first = await client.get(f"/_test/kbs/{kb.id}/documents", params={"limit": 2})
    body = first.json()
    assert first.status_code == 200
    assert set(body) == {"items", "next_cursor", "has_more"}
    assert (len(body["items"]), body["has_more"]) == (2, True)

    collected = [item["id"] for item in body["items"]]
    cursor = body["next_cursor"]
    while cursor:
        page = (
            await client.get(f"/_test/kbs/{kb.id}/documents", params={"limit": 2, "cursor": cursor})
        ).json()
        collected += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert len(set(collected)) == 5


async def test_http_bad_cursor_is_a_400_envelope(
    client: httpx.AsyncClient, session_factory: Factory
) -> None:
    kb = await kb_with_documents(session_factory, 1)

    response = await client.get(f"/_test/kbs/{kb.id}/documents", params={"cursor": "tampered"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", ["0", "101", "abc"])
async def test_http_limit_out_of_range_is_a_422(
    client: httpx.AsyncClient, session_factory: Factory, limit: str
) -> None:
    kb = await kb_with_documents(session_factory, 1)

    response = await client.get(f"/_test/kbs/{kb.id}/documents", params={"limit": limit})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["loc"] == ["query", "limit"]

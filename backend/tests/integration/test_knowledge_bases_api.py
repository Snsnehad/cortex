"""/api/v1/orgs/{org_id}/knowledge-bases (TASK-04.02).

Cross-tenant probes for these routes live in the isolation framework
(tests/integration/tenancy.py); this file covers behaviour, roles, stats and races.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentStatus, KnowledgeBase
from tests.integration.api_helpers import Api, code, team
from tests.integration.factories import create_document, create_kb

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


def kbs_path(t: dict[str, Any], suffix: str = "") -> str:
    return f"/orgs/{t['org'].id}/knowledge-bases{suffix}"


async def create(api: Api, t: dict[str, Any], *, who: str = "admin", **body: Any) -> dict[str, Any]:
    response = await api(t[who], "POST", kbs_path(t), {"name": "Handbook", **body})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# --- create ---------------------------------------------------------------------------------


async def test_admin_creates_a_knowledge_base_with_defaults(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)

    kb = await create(api, t, name="  Employee handbook  ", description="HR policies")

    assert kb["name"] == "Employee handbook"  # trimmed
    assert kb["description"] == "HR policies"
    assert kb["embedding_profile"] == "bge-small-en-v1.5__384__v1"
    assert (kb["chunk_size_tokens"], kb["chunk_overlap_tokens"]) == (512, 64)
    assert kb["created_by_user_id"] == str(t["admin"].id)
    assert kb["stats"] == {
        "document_count": 0,
        "documents_by_status": {"PENDING": 0, "PROCESSING": 0, "READY": 0, "FAILED": 0},
        "total_bytes": 0,
        "chunk_count": 0,
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"chunk_size_tokens": 256}, (256, 64)),
        ({"chunk_size_tokens": 64}, (64, 63)),  # default overlap shrinks to fit
        ({"chunk_size_tokens": 1024, "chunk_overlap_tokens": 0}, (1024, 0)),
    ],
)
async def test_chunking_settings(
    api: Api, session_factory: Factory, body: dict[str, Any], expected: tuple[int, int]
) -> None:
    t = await team(session_factory)
    kb = await create(api, t, **body)
    assert (kb["chunk_size_tokens"], kb["chunk_overlap_tokens"]) == expected


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "ok", "description": "d" * 2001},
        {"name": "ok", "chunk_size_tokens": 63},
        {"name": "ok", "chunk_size_tokens": 2049},
        {"name": "ok", "chunk_overlap_tokens": -1},
        {"name": "ok", "chunk_size_tokens": 128, "chunk_overlap_tokens": 128},
        {},
    ],
)
async def test_invalid_creates_are_422_and_create_nothing(
    api: Api, session_factory: Factory, body: dict[str, Any]
) -> None:
    t = await team(session_factory)

    response = await api(t["admin"], "POST", kbs_path(t), body)

    assert response.status_code == 422
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0


async def test_names_are_unique_per_org_ignoring_case(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)
    other = await team(session_factory)
    await create(api, t, name="Handbook")

    duplicate = await api(t["owner"], "POST", kbs_path(t), {"name": "  HANDBOOK "})

    assert code(duplicate) == (409, "KNOWLEDGE_BASE_NAME_TAKEN")
    await create(api, other, name="Handbook")  # another organization: fine


async def test_simultaneous_creates_with_one_name_exactly_one_wins(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)

    responses = await asyncio.gather(
        *(api(t[who], "POST", kbs_path(t), {"name": "Race"}) for who in ("owner", "admin"))
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeBase)) == 1


# --- read -----------------------------------------------------------------------------------


async def test_list_is_newest_first_paginated_and_open_to_members(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    names = [f"KB {i}" for i in range(5)]
    for name in names:
        await create(api, t, name=name)

    seen: list[str] = []
    cursor = None
    while True:
        query = f"?limit=2&cursor={cursor}" if cursor else "?limit=2"
        page = (await api(t["member"], "GET", kbs_path(t, query))).json()
        seen += [item["name"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break

    assert seen == list(reversed(names))


async def test_stats_count_visible_documents_only(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)
    kb_row = await create_kb(session_factory, t["org"], name="With documents")
    statuses = [
        DocumentStatus.PENDING,
        DocumentStatus.READY,
        DocumentStatus.READY,
        DocumentStatus.FAILED,
        DocumentStatus.DELETING,  # already gone for users: not counted
    ]
    documents = [await create_document(session_factory, kb_row, status=s) for s in statuses]
    async with session_factory() as session:
        for document, chunks in zip(documents[1:3], (10, 32), strict=True):
            await session.execute(
                update(Document).where(Document.id == document.id).values(chunk_count=chunks)
            )
        await session.commit()
    visible = documents[:4]

    detail = (await api(t["member"], "GET", kbs_path(t, f"/{kb_row.id}"))).json()
    listed = (await api(t["member"], "GET", kbs_path(t))).json()["items"][0]

    expected = {
        "document_count": 4,
        "documents_by_status": {"PENDING": 1, "PROCESSING": 0, "READY": 2, "FAILED": 1},
        "total_bytes": sum(d.file_size_bytes for d in visible),
        "chunk_count": 42,
    }
    assert detail["stats"] == expected
    assert listed["stats"] == expected


@pytest.fixture
def statement_count(api_app: FastAPI) -> Iterator[list[str]]:
    """Every SQL statement the app runs while the test is active."""
    statements: list[str] = []
    engine = api_app.state.db_engine.sync_engine

    def record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    yield statements
    event.remove(engine, "before_cursor_execute", record)


async def test_listing_costs_the_same_queries_for_one_or_many(
    api: Api, session_factory: Factory, statement_count: list[str]
) -> None:
    """Stats for a page come from one grouped query, not one query per knowledge base."""
    t = await team(session_factory)

    async def queries_to_list() -> int:
        statement_count.clear()
        assert (await api(t["member"], "GET", kbs_path(t))).status_code == 200
        return len(statement_count)

    kb_row = await create_kb(session_factory, t["org"])
    await create_document(session_factory, kb_row)
    one = await queries_to_list()
    for _ in range(4):
        await create_document(session_factory, await create_kb(session_factory, t["org"]))
    many = await queries_to_list()

    assert one == many


async def test_unknown_and_deleted_ids_are_404(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)
    kb = await create(api, t)
    await api(t["admin"], "DELETE", kbs_path(t, f"/{kb['id']}"))

    for kb_id in (kb["id"], "01900000-0000-7000-8000-000000000000"):
        response = await api(t["member"], "GET", kbs_path(t, f"/{kb_id}"))
        assert code(response) == (404, "KNOWLEDGE_BASE_NOT_FOUND")


# --- update ---------------------------------------------------------------------------------


async def test_partial_update_changes_only_what_is_sent(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)
    kb = await create(api, t, name="Old", description="Keep me")

    renamed = await api(t["admin"], "PATCH", kbs_path(t, f"/{kb['id']}"), {"name": "New"})
    body = renamed.json()

    assert renamed.status_code == 200
    assert (body["name"], body["description"]) == ("New", "Keep me")
    assert body["notices"] == []
    assert body["updated_at"] >= kb["updated_at"]

    cleared = await api(t["admin"], "PATCH", kbs_path(t, f"/{kb['id']}"), {"description": None})
    assert cleared.json()["description"] is None
    assert cleared.json()["name"] == "New"


async def test_chunking_change_says_it_is_not_retroactive(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    kb = await create(api, t)

    response = await api(
        t["admin"], "PATCH", kbs_path(t, f"/{kb['id']}"), {"chunk_size_tokens": 256}
    )

    body = response.json()
    assert (body["chunk_size_tokens"], body["chunk_overlap_tokens"]) == (256, 64)
    assert len(body["notices"]) == 1
    assert "existing documents keep their current chunks" in body["notices"][0]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"name": None}, (422, "VALIDATION_ERROR")),
        ({"chunk_overlap_tokens": 512}, (422, "VALIDATION_ERROR")),  # == current size
        ({"chunk_size_tokens": 64}, (422, "VALIDATION_ERROR")),  # below current overlap 64
        ({"name": "Taken"}, (409, "KNOWLEDGE_BASE_NAME_TAKEN")),
    ],
)
async def test_invalid_updates_change_nothing(
    api: Api, session_factory: Factory, body: dict[str, Any], expected: tuple[int, str]
) -> None:
    t = await team(session_factory)
    await create(api, t, name="Taken")
    kb = await create(api, t, name="Mine")

    response = await api(t["admin"], "PATCH", kbs_path(t, f"/{kb['id']}"), body)

    assert code(response) == expected
    after = (await api(t["admin"], "GET", kbs_path(t, f"/{kb['id']}"))).json()
    assert after == kb


# --- delete ---------------------------------------------------------------------------------


async def test_soft_delete_hides_at_once_and_frees_the_name(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    kb = await create(api, t, name="Doomed")
    async with session_factory() as session:
        kb_row = await session.get(KnowledgeBase, kb["id"])
    assert kb_row is not None
    document = await create_document(session_factory, kb_row)

    response = await api(t["admin"], "DELETE", kbs_path(t, f"/{kb['id']}"))

    assert response.status_code == 202
    assert response.json()["id"] == kb["id"]
    assert (await api(t["member"], "GET", kbs_path(t))).json()["items"] == []
    again = await api(t["admin"], "DELETE", kbs_path(t, f"/{kb['id']}"))
    assert code(again) == (404, "KNOWLEDGE_BASE_NOT_FOUND")
    await create(api, t, name="Doomed")  # the name is free again
    async with session_factory() as session:
        # Soft delete only: documents stay until the cleanup job (Phase 5) removes them.
        assert await session.get(Document, document.id) is not None
        deleted = await session.get(KnowledgeBase, kb["id"])
        assert deleted is not None
        assert deleted.deleted_at is not None


async def test_simultaneous_deletes_exactly_one_succeeds(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    kb = await create(api, t)

    responses = await asyncio.gather(
        *(api(t[who], "DELETE", kbs_path(t, f"/{kb['id']}")) for who in ("owner", "admin"))
    )

    assert sorted(r.status_code for r in responses) == [202, 404]


# --- roles ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "action", "allowed"),
    [
        (who, action, who != "member" or action in {"list", "get"})
        for who in ("owner", "admin", "member")
        for action in ("create", "list", "get", "update", "delete")
    ],
)
async def test_role_matrix(
    api: Api, session_factory: Factory, who: str, action: str, allowed: bool
) -> None:
    t = await team(session_factory)
    kb = await create(api, t, who="owner")
    one = kbs_path(t, f"/{kb['id']}")
    method, path, body = {
        "create": ("POST", kbs_path(t), {"name": "Another"}),
        "list": ("GET", kbs_path(t), None),
        "get": ("GET", one, None),
        "update": ("PATCH", one, {"name": "Changed"}),
        "delete": ("DELETE", one, None),
    }[action]

    response = await api(t[who], method, path, body)

    if allowed:
        assert response.status_code < 300, response.text
    else:
        assert code(response) == (403, "PERMISSION_DENIED")
        after = (await api(t["owner"], "GET", one)).json()
        assert after == kb

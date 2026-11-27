"""/api/v1/orgs: organizations, members, role rules, last-owner races (TASK-03.06)."""

import asyncio
import contextlib
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation, Organization, OrganizationMembership, OrgRole
from app.repositories.organizations import OrganizationRepository
from tests.integration.api_helpers import Api, code, team
from tests.integration.db_helpers import add_all_and_commit, new_kb
from tests.integration.factories import create_org, create_user

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


async def owners_of(factory: Factory, org: Organization) -> int:
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.role == OrgRole.OWNER,
            )
        )
    return int(count or 0)


# --- organizations --------------------------------------------------------------------------------


async def test_list_my_organizations_is_paginated_and_private(
    api: Api, session_factory: Factory
) -> None:
    me = await create_user(session_factory)
    mine = [await create_org(session_factory, owner=me, name=f"Mine {i}") for i in range(3)]
    other = await create_user(session_factory)
    await create_org(session_factory, owner=other, name="Not mine")

    first = (await api(me, "GET", "/orgs?limit=2")).json()
    rest = (await api(me, "GET", f"/orgs?limit=2&cursor={first['next_cursor']}")).json()

    names = [o["name"] for o in first["items"] + rest["items"]]
    assert names == [o.name for o in mine]  # oldest membership first, only mine
    assert (first["has_more"], rest["has_more"]) == (True, False)
    assert {o["role"] for o in first["items"]} == {"OWNER"}


async def test_create_organization_makes_the_caller_owner(
    api: Api, session_factory: Factory
) -> None:
    me = await create_user(session_factory)

    response = await api(me, "POST", "/orgs", {"name": "  Research Lab  "})

    body = response.json()
    assert response.status_code == 201
    assert (body["name"], body["slug"], body["role"]) == ("Research Lab", "research-lab", "OWNER")
    detail = await api(me, "GET", f"/orgs/{body['id']}")
    assert detail.json()["role"] == "OWNER"


@pytest.mark.parametrize("body", [{"name": "   "}, {"name": "x" * 201}, {}])
async def test_create_organization_validation(
    api: Api, session_factory: Factory, body: dict[str, str]
) -> None:
    me = await create_user(session_factory)

    assert (await api(me, "POST", "/orgs", body)).status_code == 422


async def test_get_organization_shows_the_callers_role_and_hides_others(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)

    as_admin = await api(t["admin"], "GET", f"/orgs/{t['org'].id}")
    as_outsider = await api(t["outsider"], "GET", f"/orgs/{t['org'].id}")

    assert (as_admin.status_code, as_admin.json()["role"]) == (200, "ADMIN")
    assert code(as_outsider) == (404, "ORGANIZATION_NOT_FOUND")


@pytest.mark.parametrize(("who", "status"), [("owner", 200), ("admin", 403), ("member", 403)])
async def test_only_owners_rename_and_the_slug_stays(
    api: Api, session_factory: Factory, who: str, status: int
) -> None:
    t = await team(session_factory)

    response = await api(t[who], "PATCH", f"/orgs/{t['org'].id}", {"name": "Acme Labs"})

    assert response.status_code == status
    if status == 200:
        assert (response.json()["name"], response.json()["slug"]) == ("Acme Labs", t["org"].slug)


# --- listing members ------------------------------------------------------------------------------


async def test_members_list_in_join_order_for_members_only(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)

    page = (await api(t["member"], "GET", f"/orgs/{t['org'].id}/members?limit=2")).json()
    rest = (
        await api(t["member"], "GET", f"/orgs/{t['org'].id}/members?cursor={page['next_cursor']}")
    ).json()

    listed = [(m["email"], m["role"]) for m in page["items"] + rest["items"]]
    assert listed == [
        (t["owner"].email, "OWNER"),
        (t["admin"].email, "ADMIN"),
        (t["member"].email, "MEMBER"),
    ]
    outsider = await api(t["outsider"], "GET", f"/orgs/{t['org'].id}/members")
    assert code(outsider) == (404, "ORGANIZATION_NOT_FOUND")


# --- adding members -------------------------------------------------------------------------------


async def test_admin_adds_an_existing_user_who_then_has_access(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    newcomer = await create_user(session_factory, email="New.Comer@Example.com")

    response = await api(
        t["admin"], "POST", f"/orgs/{t['org'].id}/members", {"email": "new.comer@example.com"}
    )

    assert response.status_code == 201
    assert (response.json()["user_id"], response.json()["role"]) == (str(newcomer.id), "MEMBER")
    assert (await api(newcomer, "GET", f"/orgs/{t['org'].id}")).status_code == 200


@pytest.mark.parametrize(
    ("who", "role", "expected"),
    [
        ("admin", "ADMIN", (201, None)),
        ("admin", "OWNER", (403, "PERMISSION_DENIED")),
        ("owner", "OWNER", (201, None)),
        ("member", "MEMBER", (403, "PERMISSION_DENIED")),
    ],
)
async def test_who_may_add_with_which_role(
    api: Api, session_factory: Factory, who: str, role: str, expected: tuple[int, str | None]
) -> None:
    t = await team(session_factory)
    newcomer = await create_user(session_factory)

    response = await api(
        t[who], "POST", f"/orgs/{t['org'].id}/members", {"email": newcomer.email, "role": role}
    )

    assert code(response) == expected


async def test_adding_unknown_inactive_or_existing_members(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    dormant = await create_user(session_factory, is_active=False)
    path = f"/orgs/{t['org'].id}/members"

    unknown = await api(t["admin"], "POST", path, {"email": "nobody@example.com"})
    inactive = await api(t["admin"], "POST", path, {"email": dormant.email})
    existing = await api(t["admin"], "POST", path, {"email": t["member"].email})

    assert code(unknown) == (404, "USER_NOT_FOUND")
    assert code(inactive) == (404, "USER_NOT_FOUND")
    assert code(existing) == (409, "ALREADY_MEMBER")


# --- changing roles -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "target", "new_role", "expected"),
    [
        ("admin", "member", "ADMIN", (200, None)),
        ("owner", "member", "OWNER", (200, None)),
        ("owner", "admin", "MEMBER", (200, None)),
        ("admin", "member", "OWNER", (403, "PERMISSION_DENIED")),  # only owners grant OWNER
        ("admin", "owner", "MEMBER", (403, "PERMISSION_DENIED")),  # admins can't touch owners
        ("member", "admin", "MEMBER", (403, "PERMISSION_DENIED")),
        ("owner", "owner", "ADMIN", (409, "LAST_OWNER")),  # the only owner demoting themself
    ],
)
async def test_role_change_rules(
    api: Api,
    session_factory: Factory,
    actor: str,
    target: str,
    new_role: str,
    expected: tuple[int, str | None],
) -> None:
    t = await team(session_factory)

    response = await api(
        t[actor], "PATCH", f"/orgs/{t['org'].id}/members/{t[target].id}", {"role": new_role}
    )

    assert code(response) == expected
    if expected[0] == 200:
        assert response.json()["role"] == new_role
        assert response.json()["email"] == t[target].email


async def test_an_owner_can_step_down_once_there_is_another_owner(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    path = f"/orgs/{t['org'].id}/members"
    await api(t["owner"], "PATCH", f"{path}/{t['admin'].id}", {"role": "OWNER"})

    response = await api(t["owner"], "PATCH", f"{path}/{t['owner'].id}", {"role": "ADMIN"})

    assert response.status_code == 200
    assert await owners_of(session_factory, t["org"]) == 1


async def test_changing_a_non_member_is_404(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)

    response = await api(
        t["owner"], "PATCH", f"/orgs/{t['org'].id}/members/{t['outsider'].id}", {"role": "ADMIN"}
    )

    assert code(response) == (404, "MEMBER_NOT_FOUND")


# --- removing members and leaving -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "target", "expected"),
    [
        ("member", "member", (204, None)),  # leaving
        ("admin", "member", (204, None)),
        ("owner", "admin", (204, None)),
        ("member", "admin", (403, "PERMISSION_DENIED")),
        ("admin", "owner", (403, "PERMISSION_DENIED")),
        ("owner", "owner", (409, "LAST_OWNER")),  # the only owner cannot leave
    ],
)
async def test_removal_rules(
    api: Api,
    session_factory: Factory,
    actor: str,
    target: str,
    expected: tuple[int, str | None],
) -> None:
    t = await team(session_factory)

    response = await api(t[actor], "DELETE", f"/orgs/{t['org'].id}/members/{t[target].id}")

    assert code(response) == expected
    if expected[0] == 204:
        assert (await api(t[target], "GET", f"/orgs/{t['org'].id}")).status_code == 404


async def test_removing_a_member_deletes_their_conversations_adr_020(
    api: Api, session_factory: Factory
) -> None:
    t = await team(session_factory)
    kb = new_kb(t["org"])
    await add_all_and_commit(session_factory, kb)
    conversation = Conversation(
        organization_id=t["org"].id, knowledge_base_id=kb.id, user_id=t["member"].id, title="Q"
    )
    await add_all_and_commit(session_factory, conversation)

    response = await api(t["admin"], "DELETE", f"/orgs/{t['org'].id}/members/{t['member'].id}")

    assert response.status_code == 204
    async with session_factory() as session:
        assert await session.get(Conversation, conversation.id) is None


# --- last-owner races -------------------------------------------------------------------
#
# Two in-process requests started with asyncio.gather do NOT reliably overlap at the critical
# moment: without help, the first finishes its count-then-update before the second counts,
# and the race never happens (a version of these tests passed with the lock removed). The
# hooks below force the dangerous interleaving deterministically.


async def two_owners(api: Api, session_factory: Factory) -> dict[str, Any]:
    t = await team(session_factory)
    await api(
        t["owner"], "PATCH", f"/orgs/{t['org'].id}/members/{t['admin'].id}", {"role": "OWNER"}
    )
    assert await owners_of(session_factory, t["org"]) == 2
    return t


@pytest.fixture
def owners_counted_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """After counting owners, each request waits (up to 1 s) for the other to count too.

    Without the organization lock both requests count "2 owners" and both proceed: the bug.
    With the lock the second request is still waiting for the lock, so the first gives up
    waiting at the barrier and finishes alone; the second then counts "1 owner".
    """
    barrier = asyncio.Barrier(2)
    original = OrganizationRepository.count_owners

    async def count_then_wait(self: OrganizationRepository, organization_id: uuid.UUID) -> int:
        owners = await original(self, organization_id)
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=1.0)
        return owners

    monkeypatch.setattr(OrganizationRepository, "count_owners", count_then_wait)


@pytest.mark.usefixtures("owners_counted_together")
async def test_two_owners_leaving_at_once_leave_one_owner(
    api: Api, session_factory: Factory
) -> None:
    t = await two_owners(api, session_factory)
    path = f"/orgs/{t['org'].id}/members"

    responses = await asyncio.gather(
        api(t["owner"], "DELETE", f"{path}/{t['owner'].id}"),
        api(t["admin"], "DELETE", f"{path}/{t['admin'].id}"),
    )

    assert sorted(code(r) for r in responses) == [(204, None), (409, "LAST_OWNER")]
    assert await owners_of(session_factory, t["org"]) == 1


@pytest.mark.usefixtures("owners_counted_together")
async def test_two_owners_demoting_each_other_at_once_leave_one_owner(
    api: Api, session_factory: Factory
) -> None:
    t = await two_owners(api, session_factory)
    path = f"/orgs/{t['org'].id}/members"

    responses = await asyncio.gather(
        api(t["owner"], "PATCH", f"{path}/{t['admin'].id}", {"role": "MEMBER"}),
        api(t["admin"], "PATCH", f"{path}/{t['owner'].id}", {"role": "MEMBER"}),
    )

    # Serialised by the lock: the first succeeds; the second then re-reads its own role,
    # finds it was demoted, and is refused.
    assert sorted(r.status_code for r in responses) == [200, 403]
    assert await owners_of(session_factory, t["org"]) == 1


async def test_a_role_changed_during_the_request_is_not_trusted(
    api: Api, session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actor is demoted AFTER the org-context check but BEFORE the lock (the window in
    which OrgContext.role goes stale). The decision must use the role read after the lock."""
    t = await team(session_factory)
    original_lock = OrganizationRepository.lock

    async def demote_actor_then_lock(
        self: OrganizationRepository, organization_id: uuid.UUID
    ) -> Organization | None:
        async with session_factory() as other_session:  # a concurrent, committed change
            await other_session.execute(
                update(OrganizationMembership)
                .where(OrganizationMembership.user_id == t["admin"].id)
                .values(role=OrgRole.MEMBER)
            )
            await other_session.commit()
        return await original_lock(self, organization_id)

    monkeypatch.setattr(OrganizationRepository, "lock", demote_actor_then_lock)

    response = await api(
        t["admin"], "PATCH", f"/orgs/{t['org'].id}/members/{t['member'].id}", {"role": "ADMIN"}
    )

    assert code(response) == (403, "PERMISSION_DENIED")


async def test_unknown_member_id_is_404(api: Api, session_factory: Factory) -> None:
    t = await team(session_factory)

    response = await api(t["owner"], "DELETE", f"/orgs/{t['org'].id}/members/{uuid.uuid4()}")

    assert code(response) == (404, "MEMBER_NOT_FOUND")

"""/api/v1/auth end to end: real app, real migrated database (TASK-03.04).

Cookies are handled explicitly (read Set-Cookie, send Cookie) instead of through httpx's
cookie jar, so each test controls exactly which session's refresh token is presented.
"""

import asyncio
import statistics
import time
from http.cookies import SimpleCookie
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.auth_cookies import REFRESH_COOKIE_NAME
from app.core.passwords import PasswordHasher
from app.models import Organization, OrganizationMembership, OrgRole, User

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
PASSWORD = "correct horse battery staple"
CSRF = {"X-Requested-With": "cortex"}


async def call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    token: str | None = None,
    refresh: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    client.cookies.clear()  # never send a cookie implicitly
    sent = dict(headers or {})
    if token:
        sent["Authorization"] = f"Bearer {token}"
    if refresh:
        sent["Cookie"] = f"{REFRESH_COOKIE_NAME}={refresh}"
    return await client.request(method, f"/api/v1/auth{path}", json=json, headers=sent)


def refresh_cookie(response: httpx.Response) -> SimpleCookie:
    cookie: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    return cookie


def refresh_token_from(response: httpx.Response) -> str:
    return refresh_cookie(response)[REFRESH_COOKIE_NAME].value


async def register(
    client: httpx.AsyncClient, email: str = "alice@example.com", **extra: Any
) -> httpx.Response:
    body = {"email": email, "password": PASSWORD, "full_name": "Alice Smith", **extra}
    return await call(client, "POST", "/register", json=body)


async def login(
    client: httpx.AsyncClient, email: str = "alice@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await call(client, "POST", "/login", json={"email": email, "password": password})


async def count(factory: Factory, model: type[object]) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --- register -------------------------------------------------------------------------------------


async def test_register_creates_user_org_and_owner_membership(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    response = await register(api_client, organization_name="Acme Research")

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["email"] == "alice@example.com"
    assert body["organization"] == {
        "id": body["organization"]["id"],
        "name": "Acme Research",
        "slug": "acme-research",
    }
    assert "password" not in response.text
    assert "password_hash" not in response.text

    async with session_factory() as session:
        user = await session.scalar(select(User))
        membership = await session.scalar(select(OrganizationMembership))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert membership is not None
    assert (membership.user_id, membership.role) == (user.id, OrgRole.OWNER)


async def test_register_sets_a_hardened_refresh_cookie(api_client: httpx.AsyncClient) -> None:
    response = await register(api_client)

    morsel = refresh_cookie(response)[REFRESH_COOKIE_NAME]
    assert len(morsel.value) == 43
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api/v1/auth"


async def test_default_organization_name_and_unique_slugs(api_client: httpx.AsyncClient) -> None:
    first = (await register(api_client, "a@example.com", organization_name="Acme")).json()
    second = (await register(api_client, "b@example.com", organization_name="Acme")).json()
    third = (await register(api_client, "c@example.com")).json()

    assert first["organization"]["slug"] == "acme"
    assert second["organization"]["slug"].startswith("acme-")
    assert len(second["organization"]["slug"]) == len("acme-") + 6
    assert third["organization"]["name"] == "Alice Smith's workspace"


async def test_duplicate_email_is_rejected_case_insensitively_and_atomically(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    await register(api_client, "alice@example.com")

    response = await register(api_client, "ALICE@Example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"
    assert await count(session_factory, User) == 1
    assert await count(session_factory, Organization) == 1


async def test_concurrent_identical_registrations_exactly_one_succeeds(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    responses = await asyncio.gather(*(register(api_client, "race@example.com") for _ in range(3)))

    assert sorted(r.status_code for r in responses) == [201, 409, 409]
    assert await count(session_factory, User) == 1
    assert await count(session_factory, Organization) == 1  # losers left nothing behind


@pytest.mark.parametrize(
    ("changes", "field_type"),
    [
        ({"password": "Tiny-9"}, "password_too_short"),  # distinctive: see no-echo check
        ({"password": "alice@example.com"}, "password_matches_email"),
    ],
)
async def test_password_policy_is_enforced_and_nothing_is_created(
    api_client: httpx.AsyncClient,
    session_factory: Factory,
    changes: dict[str, str],
    field_type: str,
) -> None:
    body = {"email": "alice@example.com", "password": PASSWORD, "full_name": "Alice", **changes}

    response = await call(api_client, "POST", "/register", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PASSWORD_POLICY"
    assert field_type in [f["type"] for f in response.json()["error"]["details"]["fields"]]
    assert changes["password"] not in response.text
    assert await count(session_factory, User) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": PASSWORD, "full_name": "A"},
        {"email": "a@example.com", "password": PASSWORD, "full_name": "   "},
        {"email": "a@example.com", "full_name": "A"},
    ],
)
async def test_malformed_registrations_are_422(
    api_client: httpx.AsyncClient, body: dict[str, str]
) -> None:
    response = await call(api_client, "POST", "/register", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- login ----------------------------------------------------------------------------------------


async def test_login_returns_tokens_and_records_the_time(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    await register(api_client)

    response = await login(api_client, email="  Alice@EXAMPLE.com ")

    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type", "expires_in"}
    assert refresh_token_from(response)
    async with session_factory() as session:
        assert (await session.scalar(select(User.last_login_at))) is not None


async def test_login_failures_are_indistinguishable(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    await register(api_client, "alice@example.com")
    await register(api_client, "dormant@example.com")
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.email == "dormant@example.com").values(is_active=False)
        )
        await session.commit()

    responses = [
        await login(api_client, "alice@example.com", "wrong password entirely"),
        await login(api_client, "nobody@example.com", PASSWORD),
        await login(api_client, "dormant@example.com", PASSWORD),  # correct password!
    ]

    for response in responses:
        assert response.status_code == 401
        error = response.json()["error"]
        assert (error["code"], error["message"]) == (
            "INVALID_CREDENTIALS",
            "Incorrect email or password.",
        )
        assert "set-cookie" not in response.headers


async def test_unknown_email_takes_as_long_as_a_wrong_password(
    api_client: httpx.AsyncClient,
) -> None:
    await register(api_client)
    await login(api_client, "warm-up@example.com", "x" * 12)  # creates the dummy hash

    async def median_ms(email: str) -> float:
        samples = []
        for _ in range(5):
            started = time.perf_counter()
            await login(api_client, email, "wrong password entirely")
            samples.append((time.perf_counter() - started) * 1000)
        return statistics.median(samples)

    known, unknown = await median_ms("alice@example.com"), await median_ms("nobody@example.com")

    # Same order of magnitude; without dummy_verify "unknown" would be ~50 ms faster.
    assert 0.5 < unknown / known < 2.0


async def test_login_upgrades_an_outdated_password_hash(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    await register(api_client)
    old_hash = PasswordHasher(time_cost=1, memory_cost_kib=8 * 1024).hash(PASSWORD)
    async with session_factory() as session:
        await session.execute(update(User).values(password_hash=old_hash))
        await session.commit()

    assert (await login(api_client)).status_code == 200

    async with session_factory() as session:
        stored = await session.scalar(select(User.password_hash))
    assert stored is not None
    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")


# --- /me ------------------------------------------------------------------------------------------


async def test_me_requires_a_valid_bearer_token(api_client: httpx.AsyncClient) -> None:
    missing = await call(api_client, "GET", "/me")
    garbage = await call(api_client, "GET", "/me", token="not-a-token")
    basic = await call(api_client, "GET", "/me", headers={"Authorization": "Basic YTpi"})

    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "UNAUTHENTICATED")
    assert missing.headers["www-authenticate"] == "Bearer"
    assert (garbage.status_code, garbage.json()["error"]["code"]) == (401, "INVALID_TOKEN")
    assert basic.status_code == 401


async def test_me_returns_the_user_and_memberships(api_client: httpx.AsyncClient) -> None:
    token = (await register(api_client, organization_name="Acme")).json()["access_token"]

    response = await call(api_client, "GET", "/me", token=token)

    body = response.json()
    assert response.status_code == 200
    assert body["user"]["email"] == "alice@example.com"
    assert [(m["organization"]["slug"], m["role"]) for m in body["memberships"]] == [
        ("acme", "OWNER")
    ]
    assert "password_hash" not in response.text


async def test_deactivated_user_is_locked_out_immediately(
    api_client: httpx.AsyncClient, session_factory: Factory
) -> None:
    token = (await register(api_client)).json()["access_token"]
    async with session_factory() as session:
        await session.execute(update(User).values(is_active=False))
        await session.commit()

    response = await call(api_client, "GET", "/me", token=token)  # token itself still valid

    assert (response.status_code, response.json()["error"]["code"]) == (401, "INVALID_TOKEN")


async def test_update_profile(api_client: httpx.AsyncClient) -> None:
    token = (await register(api_client)).json()["access_token"]

    updated = await call(api_client, "PATCH", "/me", token=token, json={"full_name": "  Alice M  "})
    blank = await call(api_client, "PATCH", "/me", token=token, json={"full_name": " "})

    assert updated.json()["user"]["full_name"] == "Alice M"
    assert blank.status_code == 422


# --- refresh and logout ---------------------------------------------------------------------------


async def test_refresh_requires_the_csrf_header(api_client: httpx.AsyncClient) -> None:
    refresh = refresh_token_from(await register(api_client))

    response = await call(api_client, "POST", "/refresh", refresh=refresh)

    assert (response.status_code, response.json()["error"]["code"]) == (403, "CSRF_CHECK_FAILED")


async def test_refresh_without_a_cookie_is_401(api_client: httpx.AsyncClient) -> None:
    response = await call(api_client, "POST", "/refresh", headers=CSRF)

    assert (response.status_code, response.json()["error"]["code"]) == (
        401,
        "INVALID_REFRESH_TOKEN",
    )


async def test_refresh_rotates_and_reuse_ends_the_session(api_client: httpx.AsyncClient) -> None:
    first = refresh_token_from(await register(api_client))

    rotated = await call(api_client, "POST", "/refresh", refresh=first, headers=CSRF)
    second = refresh_token_from(rotated)
    replay = await call(api_client, "POST", "/refresh", refresh=first, headers=CSRF)
    after = await call(api_client, "POST", "/refresh", refresh=second, headers=CSRF)

    assert rotated.status_code == 200
    assert rotated.json()["access_token"]
    assert second != first
    assert (replay.status_code, replay.json()["error"]["code"]) == (401, "INVALID_REFRESH_TOKEN")
    assert after.status_code == 401  # the whole family was revoked


async def test_logout_ends_the_session_and_clears_the_cookie(
    api_client: httpx.AsyncClient,
) -> None:
    refresh = refresh_token_from(await register(api_client))

    response = await call(api_client, "POST", "/logout", refresh=refresh)
    again = await call(api_client, "POST", "/logout")
    after = await call(api_client, "POST", "/refresh", refresh=refresh, headers=CSRF)

    assert response.status_code == 204
    cleared = refresh_cookie(response)[REFRESH_COOKIE_NAME]
    assert (cleared.value, cleared["max-age"], cleared["path"]) == ("", "0", "/api/v1/auth")
    assert again.status_code == 204  # idempotent, no cookie needed
    assert after.status_code == 401


# --- change password ------------------------------------------------------------------------------


async def test_change_password_keeps_this_session_and_ends_the_others(
    api_client: httpx.AsyncClient,
) -> None:
    registered = await register(api_client)
    token, this_session = registered.json()["access_token"], refresh_token_from(registered)
    other_session = refresh_token_from(await login(api_client))  # e.g. a phone

    response = await call(
        api_client,
        "POST",
        "/me/password",
        token=token,
        refresh=this_session,
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )

    assert response.status_code == 204
    assert (await login(api_client)).status_code == 401
    assert (await login(api_client, password="a brand new passphrase")).status_code == 200
    phone = await call(api_client, "POST", "/refresh", refresh=other_session, headers=CSRF)
    laptop = await call(api_client, "POST", "/refresh", refresh=this_session, headers=CSRF)
    assert phone.status_code == 401
    assert laptop.status_code == 200


async def test_change_password_requires_the_current_password(
    api_client: httpx.AsyncClient,
) -> None:
    token = (await register(api_client)).json()["access_token"]

    response = await call(
        api_client,
        "POST",
        "/me/password",
        token=token,
        json={"current_password": "not my password", "new_password": "a brand new passphrase"},
    )

    error = response.json()["error"]
    assert (response.status_code, error["code"]) == (422, "INVALID_CURRENT_PASSWORD")
    assert error["details"]["fields"][0]["loc"] == ["body", "current_password"]
    assert (await login(api_client)).status_code == 200  # unchanged


async def test_change_password_enforces_the_policy(api_client: httpx.AsyncClient) -> None:
    token = (await register(api_client)).json()["access_token"]

    response = await call(
        api_client,
        "POST",
        "/me/password",
        token=token,
        json={"current_password": PASSWORD, "new_password": "short"},
    )

    assert (response.status_code, response.json()["error"]["code"]) == (422, "PASSWORD_POLICY")

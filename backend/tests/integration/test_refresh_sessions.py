"""Refresh-token sessions against PostgreSQL: rotation, reuse detection, races (TASK-03.03)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.logging import configure_logging
from app.core.refresh_tokens import (
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    hash_refresh_token,
)
from app.models import RefreshToken, User
from app.services.sessions import IssuedRefreshToken, SessionService
from tests.integration.factories import create_user

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]
TTL_DAYS = 14


async def start(
    factory: Factory, user: User, *, user_agent: str | None = None
) -> IssuedRefreshToken:
    async with factory() as session:
        return await SessionService(session, ttl_days=TTL_DAYS).start(
            user.id, user_agent=user_agent
        )


async def rotate(factory: Factory, token: str) -> IssuedRefreshToken:
    async with factory() as session:
        return await SessionService(session, ttl_days=TTL_DAYS).rotate(token)


async def end(factory: Factory, token: str) -> None:
    async with factory() as session:
        await SessionService(session, ttl_days=TTL_DAYS).end(token)


async def family_rows(factory: Factory, issued: IssuedRefreshToken) -> list[RefreshToken]:
    async with factory() as session:
        rows = await session.scalars(
            select(RefreshToken)
            .where(RefreshToken.family_id == issued.family_id)
            .order_by(RefreshToken.created_at, RefreshToken.id)
        )
        return list(rows.all())


async def active_count(factory: Factory, issued: IssuedRefreshToken) -> int:
    return sum(1 for row in await family_rows(factory, issued) if row.revoked_at is None)


# --- issue ----------------------------------------------------------------------------------------


async def test_start_stores_only_the_hash(session_factory: Factory) -> None:
    user = await create_user(session_factory)

    issued = await start(session_factory, user, user_agent="Mozilla/5.0 " + "x" * 500)

    [row] = await family_rows(session_factory, issued)
    assert row.token_hash == hash_refresh_token(issued.token)
    assert issued.token not in {row.token_hash, row.user_agent}
    assert row.user_agent is not None
    assert len(row.user_agent) == 300  # truncated
    assert abs(row.expires_at - (datetime.now(UTC) + timedelta(days=TTL_DAYS))) < timedelta(
        minutes=1
    )


async def test_each_login_is_its_own_family(session_factory: Factory) -> None:
    user = await create_user(session_factory)

    laptop, phone = await start(session_factory, user), await start(session_factory, user)

    assert laptop.family_id != phone.family_id


# --- rotation -------------------------------------------------------------------------------------


async def test_rotation_replaces_the_token_within_the_family(session_factory: Factory) -> None:
    user = await create_user(session_factory)
    first = await start(session_factory, user)

    second = await rotate(session_factory, first.token)

    assert second.token != first.token
    assert (second.user_id, second.family_id) == (user.id, first.family_id)
    old, new = await family_rows(session_factory, first)
    assert old.revoked_at is not None
    assert old.replaced_by_id == new.id
    assert new.revoked_at is None


async def test_a_chain_of_rotations_leaves_exactly_one_active_token(
    session_factory: Factory,
) -> None:
    user = await create_user(session_factory)
    token = await start(session_factory, user)
    for _ in range(3):
        token = await rotate(session_factory, token.token)

    assert len(await family_rows(session_factory, token)) == 4
    assert await active_count(session_factory, token) == 1


async def test_unknown_token_is_rejected(session_factory: Factory) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, "never-issued-token")


async def test_expired_token_is_rejected_and_not_rotated(session_factory: Factory) -> None:
    user = await create_user(session_factory)
    issued = await start(session_factory, user)
    async with session_factory() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == issued.family_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    with pytest.raises(RefreshTokenExpiredError):
        await rotate(session_factory, issued.token)
    assert len(await family_rows(session_factory, issued)) == 1


async def test_inactive_user_cannot_refresh_and_loses_the_session(
    session_factory: Factory,
) -> None:
    user = await create_user(session_factory)
    issued = await start(session_factory, user)
    async with session_factory() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()

    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, issued.token)
    assert await active_count(session_factory, issued) == 0


# --- reuse detection ------------------------------------------------------------------------------


async def test_reusing_an_old_token_revokes_the_whole_family(session_factory: Factory) -> None:
    user = await create_user(session_factory)
    stolen = await start(session_factory, user)
    current = await rotate(session_factory, stolen.token)  # legitimate user moved on

    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, stolen.token)  # attacker replays the old token

    # The revocation was committed despite the error, so the legitimate user's newest token
    # is dead too: both parties must sign in again, ending the stolen session.
    assert await active_count(session_factory, current) == 0
    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, current.token)


async def test_reuse_in_one_family_leaves_other_devices_signed_in(
    session_factory: Factory,
) -> None:
    user = await create_user(session_factory)
    laptop = await start(session_factory, user)
    phone = await start(session_factory, user)
    await rotate(session_factory, laptop.token)

    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, laptop.token)

    assert await active_count(session_factory, phone) == 1
    assert (await rotate(session_factory, phone.token)).family_id == phone.family_id


async def test_reuse_is_logged_without_the_token(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    # Configure JSON logging here: relying on an earlier test having done it would make
    # this test depend on the (random) test order.
    configure_logging(Settings(_env_file=None))
    user = await create_user(session_factory)
    stolen = await start(session_factory, user)
    current = await rotate(session_factory, stolen.token)
    capsys.readouterr()

    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, stolen.token)

    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    [event] = [e for e in events if e["event"] == "auth.refresh_token_reuse_detected"]
    assert event["level"] == "warning"
    assert event["family_id"] == str(stolen.family_id)
    assert event["tokens_revoked"] == 1
    assert stolen.token not in output
    assert current.token not in output


async def test_concurrent_double_use_one_wins_then_the_family_is_revoked(
    session_factory: Factory,
) -> None:
    user = await create_user(session_factory)
    issued = await start(session_factory, user)

    async def attempt() -> IssuedRefreshToken | None:
        try:
            return await rotate(session_factory, issued.token)
        except InvalidRefreshTokenError:
            return None

    outcomes = await asyncio.gather(attempt(), attempt())

    # FOR UPDATE serialises the two: the first rotates, the second then sees a replaced token,
    # which is indistinguishable from theft (ADR-022: strict, no grace period).
    assert sum(outcome is not None for outcome in outcomes) == 1
    assert await active_count(session_factory, issued) == 0


# --- logout ---------------------------------------------------------------------------------------


async def test_logout_revokes_the_family_and_is_idempotent(session_factory: Factory) -> None:
    user = await create_user(session_factory)
    issued = await start(session_factory, user)
    rotated = await rotate(session_factory, issued.token)

    await end(session_factory, rotated.token)
    await end(session_factory, rotated.token)
    await end(session_factory, "unknown-token")

    assert await active_count(session_factory, issued) == 0
    with pytest.raises(InvalidRefreshTokenError):
        await rotate(session_factory, rotated.token)


async def test_end_all_sessions_can_keep_the_current_one(session_factory: Factory) -> None:
    user = await create_user(session_factory)
    current = await start(session_factory, user)
    others = [await start(session_factory, user) for _ in range(2)]

    async with session_factory() as session:
        revoked = await SessionService(session, ttl_days=TTL_DAYS).end_all_for_user(
            user.id, keep_family=current.family_id
        )

    assert revoked == 2
    assert await active_count(session_factory, current) == 1
    assert all([await active_count(session_factory, o) == 0 for o in others])

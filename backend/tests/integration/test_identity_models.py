"""Users, organizations, memberships and refresh tokens against the migrated schema (TASK-02.03).

Runs on a scratch database built by the real Alembic migration (``migrated_database_url``),
so these tests verify what production gets, including database-enforced rules.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError, StatementError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.models import Organization, OrganizationMembership, OrgRole, RefreshToken, User

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, organizations, organization_memberships, refresh_tokens CASCADE")
        )
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def new_user(email: str = "alice@example.com") -> User:
    return User(email=email, password_hash="argon2-hash-placeholder", full_name="Alice")


def new_org(slug: str = "acme", creator: User | None = None) -> Organization:
    return Organization(
        name=slug.title(), slug=slug, created_by_user_id=creator.id if creator else None
    )


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint PostgreSQL reported, proving *which* rule fired."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


async def add_all_and_commit(factory: async_sessionmaker[AsyncSession], *objs: object) -> None:
    async with factory() as session:
        session.add_all(objs)
        await session.commit()


# --- users --------------------------------------------------------------------------------


async def test_user_gets_uuid7_server_defaults_and_timestamps(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = new_user()
    await add_all_and_commit(session_factory, user)

    assert user.id.version == 7
    assert user.is_active is True  # server default, fetched via RETURNING
    assert user.created_at.tzinfo is not None
    assert user.last_login_at is None


async def test_different_emails_can_both_register(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Guards the case-insensitive email index itself. An index on lower('email') (a string
    # literal instead of the column) is "unique" over a constant and would allow ONE user.
    await add_all_and_commit(
        session_factory, new_user("alice@example.com"), new_user("bob@example.com")
    )

    async with session_factory() as session:
        assert len((await session.scalars(select(User))).all()) == 2


async def test_email_is_unique_case_insensitively(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_all_and_commit(session_factory, new_user("Alice@Example.com"))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_user("alice@EXAMPLE.com"))

    assert violated_constraint(exc_info.value) == "uq_users_email_lower"


async def test_email_is_stored_as_entered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_all_and_commit(session_factory, new_user("Alice.Smith@Example.com"))

    async with session_factory() as session:
        assert await session.scalar(select(User.email)) == "Alice.Smith@Example.com"


# --- organizations and memberships --------------------------------------------------------


async def test_organization_slug_is_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_all_and_commit(session_factory, new_org("acme"))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_org("acme"))

    assert violated_constraint(exc_info.value) == "uq_organizations_slug"


async def test_role_round_trips_as_enum(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = new_user(), new_org()
    await add_all_and_commit(session_factory, user, org)
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.ADMIN),
    )

    async with session_factory() as session:
        role = await session.scalar(select(OrganizationMembership.role))
    assert role is OrgRole.ADMIN


async def test_user_cannot_join_the_same_organization_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = new_user(), new_org()
    await add_all_and_commit(session_factory, user, org)
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER),
    )

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(
            session_factory,
            OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
        )

    assert (
        violated_constraint(exc_info.value) == "uq_organization_memberships_organization_id_user_id"
    )


async def test_database_rejects_unknown_roles_even_bypassing_the_orm(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = new_user(), new_org()
    await add_all_and_commit(session_factory, user, org)

    with pytest.raises(IntegrityError) as exc_info:
        async with session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO organization_memberships (id, organization_id, user_id, role) "
                    "VALUES (:id, :org, :user, 'SUPERUSER')"
                ),
                {"id": uuid.uuid4(), "org": org.id, "user": user.id},
            )

    assert violated_constraint(exc_info.value) == "ck_organization_memberships_role"


async def test_orm_rejects_unknown_roles_before_sql_is_sent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = new_user(), new_org()
    await add_all_and_commit(session_factory, user, org)

    with pytest.raises(StatementError, match="SUPERUSER"):
        await add_all_and_commit(
            session_factory,
            OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role="SUPERUSER",  # deliberately not an OrgRole
            ),
        )


async def test_membership_requires_existing_user_and_organization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(
            session_factory,
            OrganizationMembership(
                organization_id=uuid.uuid4(), user_id=uuid.uuid4(), role=OrgRole.MEMBER
            ),
        )

    assert (violated_constraint(exc_info.value) or "").startswith("fk_organization_memberships_")


# --- deletes --------------------------------------------------------------------------------


async def test_deleting_a_user_cascades_and_keeps_their_organizations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = new_user()
    await add_all_and_commit(session_factory, user)
    org = new_org(creator=user)
    await add_all_and_commit(session_factory, org)
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER),
        RefreshToken(
            user_id=user.id,
            family_id=uuid.uuid4(),
            token_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(days=14),
        ),
    )

    # ORM delete: with passive_deletes + lazy="raise" SQLAlchemy must NOT try to load the
    # collections; PostgreSQL's ON DELETE rules do the work.
    async with session_factory() as session:
        await session.delete(await session.get_one(User, user.id))
        await session.commit()

    async with session_factory() as session:
        assert (await session.scalars(select(OrganizationMembership))).all() == []
        assert (await session.scalars(select(RefreshToken))).all() == []
        surviving_org = await session.get_one(Organization, org.id)
        assert surviving_org.created_by_user_id is None  # ON DELETE SET NULL


async def test_deleting_an_organization_removes_its_memberships_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, acme, globex = new_user(), new_org("acme"), new_org("globex")
    await add_all_and_commit(session_factory, user, acme, globex)
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=acme.id, user_id=user.id, role=OrgRole.OWNER),
        OrganizationMembership(organization_id=globex.id, user_id=user.id, role=OrgRole.MEMBER),
    )

    async with session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == acme.id))
        await session.commit()

    async with session_factory() as session:
        remaining = (await session.scalars(select(OrganizationMembership))).all()
        assert [m.organization_id for m in remaining] == [globex.id]
        assert await session.get(User, user.id) is not None


# --- refresh tokens -------------------------------------------------------------------------


async def test_token_hash_is_unique_and_rotation_link_survives_deletion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = new_user()
    await add_all_and_commit(session_factory, user)
    family = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(days=14)
    newer = RefreshToken(user_id=user.id, family_id=family, token_hash="b" * 64, expires_at=expires)
    await add_all_and_commit(session_factory, newer)
    older = RefreshToken(
        user_id=user.id,
        family_id=family,
        token_hash="c" * 64,
        expires_at=expires,
        replaced_by_id=newer.id,
    )
    await add_all_and_commit(session_factory, older)

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(
            session_factory,
            RefreshToken(
                user_id=user.id, family_id=family, token_hash="b" * 64, expires_at=expires
            ),
        )
    assert violated_constraint(exc_info.value) == "uq_refresh_tokens_token_hash"

    async with session_factory() as session:
        await session.execute(delete(RefreshToken).where(RefreshToken.id == newer.id))
        await session.commit()
    async with session_factory() as session:
        assert (await session.get_one(RefreshToken, older.id)).replaced_by_id is None


# --- relationship loading under asyncio -----------------------------------------------------


async def test_relationships_must_be_loaded_explicitly(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = new_user(), new_org()
    await add_all_and_commit(session_factory, user, org)
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER),
    )

    async with session_factory() as session:
        loaded = await session.get_one(User, user.id)
        with pytest.raises(InvalidRequestError, match="lazy='raise'"):
            _ = loaded.memberships

    async with session_factory() as session:
        eager = await session.scalar(
            select(User)
            .where(User.id == user.id)
            .options(
                selectinload(User.memberships).selectinload(OrganizationMembership.organization)
            )
        )
        assert eager is not None
        assert [(m.role, m.organization.slug) for m in eager.memberships] == [
            (OrgRole.OWNER, "acme")
        ]

"""Pure-Python behaviour of the identity models (TASK-02.03)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.models import OrganizationMembership, OrgRole, RefreshToken, User


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        (OrgRole.OWNER, OrgRole.ADMIN, True),
        (OrgRole.ADMIN, OrgRole.ADMIN, True),
        (OrgRole.MEMBER, OrgRole.ADMIN, False),
        (OrgRole.ADMIN, OrgRole.OWNER, False),
        (OrgRole.MEMBER, OrgRole.MEMBER, True),
    ],
)
def test_role_hierarchy(role: OrgRole, minimum: OrgRole, allowed: bool) -> None:
    assert role.at_least(minimum) is allowed


def test_roles_are_plain_strings_for_json_and_sql() -> None:
    assert OrgRole("ADMIN") is OrgRole.ADMIN
    assert f"{OrgRole.OWNER}" == "OWNER"


def test_reprs_never_contain_secrets_or_personal_data() -> None:
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        password_hash="$argon2id$v=19$secret",
        full_name="Alice",
    )
    token = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash="f" * 64,
        expires_at=datetime.now(UTC),
    )
    membership = OrganizationMembership(
        organization_id=uuid.uuid4(), user_id=user.id, role=OrgRole.MEMBER
    )

    rendered = repr(user) + repr(token) + repr(membership)

    for sensitive in ("alice@example.com", "$argon2id", "f" * 64, "Alice"):
        assert sensitive not in rendered

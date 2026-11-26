"""Signed pagination cursors (TASK-02.07)."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.pagination import (
    MAX_CURSOR_LENGTH,
    Cursor,
    CursorCodec,
    InvalidCursorError,
    SortValue,
)
from app.repositories.pagination import PageRequest

SCOPE = "documents:kb=123:created_at:desc"
codec = CursorCodec(b"test-secret-at-least-16-bytes")


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 11, 26, 12, 30, 45, 123456, tzinfo=UTC),
        datetime(2026, 11, 26, 18, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        "Handbook",
        42,
        uuid.uuid4(),
    ],
)
def test_round_trip_preserves_value_and_type(value: SortValue) -> None:
    cursor = Cursor(scope=SCOPE, sort_value=value, id=uuid.uuid4())

    decoded = codec.decode(codec.encode(cursor), scope=SCOPE)

    assert decoded == cursor
    assert type(decoded.sort_value) is type(value)


def test_cursor_is_url_safe() -> None:
    token = codec.encode(Cursor(SCOPE, "a/b+c=d?&", uuid.uuid4()))

    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")


def _forge(token: str, **changes: object) -> str:
    payload_part, signature_part = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)))
    data.update(changes)
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{payload}.{signature_part}"


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda t: _forge(t, k="2000-01-01T00:00:00+00:00"), id="edited-sort-value"),
        pytest.param(lambda t: _forge(t, i=str(uuid.uuid4())), id="edited-id"),
        pytest.param(lambda t: _forge(t, s="documents:kb=999:created_at:desc"), id="edited-scope"),
        pytest.param(lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"), id="edited-sig"),
        pytest.param(lambda t: t.split(".")[0], id="signature-removed"),
        pytest.param(lambda t: t + ".extra", id="extra-part"),
        pytest.param(lambda t: "not-a-cursor", id="garbage"),
        pytest.param(lambda t: "%%%.$$$", id="invalid-base64"),
        pytest.param(lambda t: "", id="empty"),
        pytest.param(lambda t: "A" * (MAX_CURSOR_LENGTH + 1), id="too-long"),
    ],
)
def test_tampered_or_malformed_cursors_are_rejected(mangle: object) -> None:
    token = codec.encode(Cursor(SCOPE, datetime.now(UTC), uuid.uuid4()))

    with pytest.raises(InvalidCursorError) as exc_info:
        codec.decode(mangle(token), scope=SCOPE)  # type: ignore[operator]
    assert exc_info.value.code == "INVALID_CURSOR"


def test_cursor_from_another_listing_is_rejected() -> None:
    token = codec.encode(Cursor(SCOPE, "x", uuid.uuid4()))

    with pytest.raises(InvalidCursorError):
        codec.decode(token, scope="documents:kb=999:created_at:desc")


def test_cursor_signed_with_another_secret_is_rejected() -> None:
    other = CursorCodec(b"a-completely-different-secret")
    token = other.encode(Cursor(SCOPE, "x", uuid.uuid4()))

    with pytest.raises(InvalidCursorError):
        codec.decode(token, scope=SCOPE)


def test_naive_datetimes_cannot_be_encoded() -> None:
    with pytest.raises(ValueError, match="naive"):
        codec.encode(Cursor(SCOPE, datetime(2026, 1, 1), uuid.uuid4()))  # noqa: DTZ001


def test_short_secrets_are_refused() -> None:
    with pytest.raises(ValueError, match="too short"):
        CursorCodec(b"short")


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_page_request_limit_bounds(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        PageRequest(limit=limit)

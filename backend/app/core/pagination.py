"""Keyset pagination cursors (API_SPECIFICATION.md §1, ADR-014).

A cursor marks "the last row the client has seen" as ``(sort_value, id)``. The next page is
``WHERE (sort_key, id) < (sort_value, id)`` (or ``>`` ascending), which is stable while rows
are inserted or deleted between requests: no duplicates, no skipped rows, and every page
costs the same (no growing OFFSET scan).

Cursors are **signed, not encrypted**. A client can base64-decode one and see the sort value
and id, which is harmless, but cannot forge or alter it: the HMAC covers the payload, which
includes a *scope* string naming the listing (e.g. ``documents:kb=<id>:created_at:desc``), so
a cursor from one listing is rejected on another. Tenant isolation never relies on cursors;
every query still filters by organization.

Format: ``base64url(json payload) "." base64url(hmac_sha256(payload)[:16])``.
"""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.errors import BadRequestError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 1024  # reject absurd input before any decoding work
_SIGNATURE_BYTES = 16  # 128-bit truncated HMAC-SHA256: ample against forgery
_KEY_CONTEXT = b"cortex/pagination-cursor/v1"
_VERSION = 1

SortValue = datetime | str | int | uuid.UUID


class InvalidCursorError(BadRequestError):
    default_code = "INVALID_CURSOR"
    default_message = "The pagination cursor is invalid or has expired. Restart from page one."


@dataclass(frozen=True, slots=True)
class Cursor:
    scope: str
    sort_value: SortValue
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PageResult[T]:
    items: Sequence[T]
    next_cursor: str | None
    has_more: bool


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _encode_value(value: SortValue) -> tuple[str, Any]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetimes cannot be used as cursor values")
        return "dt", value.isoformat()
    if isinstance(value, uuid.UUID):
        return "uuid", str(value)
    if isinstance(value, bool):  # bool is an int subclass; never a valid sort key here
        raise TypeError("bool is not a supported cursor value")
    if isinstance(value, int):
        return "int", value
    if isinstance(value, str):
        return "str", value
    raise TypeError(f"unsupported cursor value type: {type(value).__name__}")


def _decode_value(tag: Any, raw: Any) -> SortValue:
    if tag == "dt" and isinstance(raw, str):
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None:
            raise ValueError("naive datetime in cursor")
        return value
    if tag == "uuid" and isinstance(raw, str):
        return uuid.UUID(raw)
    if tag == "int" and isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if tag == "str" and isinstance(raw, str):
        return raw
    raise ValueError("unknown cursor value type")


class CursorCodec:
    """Encodes and verifies cursors. One instance per process; the key never leaves it."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            raise ValueError("cursor secret too short")
        # Derive a dedicated key so the pagination key never equals the JWT signing key.
        self._key = hmac.new(secret, _KEY_CONTEXT, hashlib.sha256).digest()

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]

    def encode(self, cursor: Cursor) -> str:
        tag, value = _encode_value(cursor.sort_value)
        payload = json.dumps(
            {"v": _VERSION, "s": cursor.scope, "t": tag, "k": value, "i": str(cursor.id)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return f"{_b64encode(payload)}.{_b64encode(self._sign(payload))}"

    def decode(self, token: str, *, scope: str) -> Cursor:
        """Verify and decode, or raise InvalidCursorError (one error for every failure, so
        the response reveals nothing about *why* a cursor was rejected)."""
        try:
            if len(token) > MAX_CURSOR_LENGTH:
                raise ValueError("too long")
            payload_part, signature_part = token.split(".")
            payload = _b64decode(payload_part)
            if not hmac.compare_digest(_b64decode(signature_part), self._sign(payload)):
                raise ValueError("bad signature")
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("v") != _VERSION:
                raise ValueError("unsupported version")
            if data.get("s") != scope:
                raise ValueError("cursor belongs to another listing")
            return Cursor(
                scope=scope,
                sort_value=_decode_value(data.get("t"), data.get("k")),
                id=uuid.UUID(str(data.get("i"))),
            )
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
            raise InvalidCursorError from exc

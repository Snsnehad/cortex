"""Identifier generation.

Primary keys are UUIDv7 (RFC 9562): the first 48 bits are a Unix timestamp in milliseconds,
the rest is random. Compared with random UUIDv4 keys this keeps B-tree index inserts at the
"right edge" of the index (better cache locality, fewer page splits) and makes ids roughly
sortable by creation time, while staying globally unique and unguessable enough for use in
URLs (74 random bits per millisecond).

Python gains ``uuid.uuid7()`` in 3.14; this implementation follows the same layout so it can
be swapped out later without changing stored data.

Layout (128 bits):  unix_ts_ms (48) | ver=0b0111 (4) | rand_a (12) | var=0b10 (2) | rand_b (62)
"""

import secrets
import time
import uuid

_TIMESTAMP_MASK = (1 << 48) - 1


def uuid7(timestamp_ms: int | None = None) -> uuid.UUID:
    """Return a new time-ordered UUID (version 7)."""
    ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        (ms & _TIMESTAMP_MASK) << 80
        | 0x7 << 76  # version 7
        | rand_a << 64
        | 0b10 << 62  # RFC 9562 variant
        | rand_b
    )
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Milliseconds since the Unix epoch encoded in a UUIDv7."""
    if value.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    return value.int >> 80

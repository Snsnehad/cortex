"""UUIDv7 generation (TASK-02.01)."""

import time
import uuid

import pytest

from app.utils.ids import uuid7, uuid7_timestamp_ms


def test_version_and_variant_follow_rfc_9562() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_timestamp_round_trips() -> None:
    assert uuid7_timestamp_ms(uuid7(timestamp_ms=1_700_000_000_123)) == 1_700_000_000_123


def test_timestamp_is_current_time() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000

    assert before <= uuid7_timestamp_ms(value) <= after


def test_ids_sort_by_creation_time_as_uuids_and_as_strings() -> None:
    ids = [uuid7(timestamp_ms=ms) for ms in (1_000, 2_000, 3_000, 1_700_000_000_000)]

    assert sorted(ids) == ids
    assert sorted(str(value) for value in ids) == [str(value) for value in ids]


def test_ids_are_unique_within_the_same_millisecond() -> None:
    values = {uuid7(timestamp_ms=42) for _ in range(10_000)}

    assert len(values) == 10_000


def test_timestamp_extraction_rejects_other_versions() -> None:
    with pytest.raises(ValueError, match="not a UUIDv7"):
        uuid7_timestamp_ms(uuid.uuid4())

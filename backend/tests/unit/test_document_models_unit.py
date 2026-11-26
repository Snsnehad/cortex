"""Pure-Python behaviour of the document models (TASK-02.04)."""

import uuid

from app.models import ACTIVE_JOB_STATUSES, JobStatus, chunk_id


def test_chunk_id_is_deterministic() -> None:
    document_id = uuid.uuid4()

    assert chunk_id(document_id, "profile-a", 3) == chunk_id(document_id, "profile-a", 3)


def test_chunk_id_differs_by_document_profile_and_index() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    ids = {
        chunk_id(doc_a, "profile-a", 0),
        chunk_id(doc_a, "profile-a", 1),
        chunk_id(doc_a, "profile-b", 0),
        chunk_id(doc_b, "profile-a", 0),
    }

    assert len(ids) == 4


def test_chunk_id_is_a_name_based_uuid() -> None:
    assert chunk_id(uuid.uuid4(), "p", 0).version == 5


def test_active_job_statuses_match_the_partial_unique_index() -> None:
    # Must mirror uq_processing_jobs_document_id_active's WHERE clause.
    assert {status.value for status in ACTIVE_JOB_STATUSES} == {"QUEUED", "RUNNING", "RETRYING"}
    assert JobStatus.RUNNING.is_active
    assert not JobStatus.SUCCEEDED.is_active

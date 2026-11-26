"""Knowledge bases, documents, chunks and processing jobs (TASK-02.04).

Runs on the scratch database built by the real migrations. The most important property
tested here is that tenant boundaries are enforced by PostgreSQL itself: even buggy
application code cannot attach a document, chunk or job to another organization's data.
"""

import asyncio
import uuid
import warnings
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    JobStage,
    JobStatus,
    JobType,
    KnowledgeBase,
    Organization,
    ProcessingJob,
    User,
    chunk_id,
)
from tests.integration.db_helpers import (
    TEST_EMBEDDING_PROFILE,
    add_all_and_commit,
    new_document,
    new_kb,
    new_org,
    new_user,
    sha256_hex,
    violated_constraint,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

Factory = async_sessionmaker[AsyncSession]


def new_chunk(
    document: Document,
    index: int = 0,
    *,
    profile: str = TEST_EMBEDDING_PROFILE,
    organization_id: uuid.UUID | None = None,
    knowledge_base_id: uuid.UUID | None = None,
    char_start: int = 0,
    char_end: int = 100,
    page_start: int | None = 1,
    page_end: int | None = 1,
) -> DocumentChunk:
    body = f"chunk {index} of {document.id}"
    return DocumentChunk(
        id=chunk_id(document.id, profile, index),
        organization_id=organization_id or document.organization_id,
        knowledge_base_id=knowledge_base_id or document.knowledge_base_id,
        document_id=document.id,
        embedding_profile=profile,
        chunk_index=index,
        text=body,
        token_count=12,
        char_start=char_start,
        char_end=char_end,
        page_start=page_start,
        page_end=page_end,
        content_hash=sha256_hex(body),
    )


def new_job(
    document: Document,
    status: JobStatus = JobStatus.QUEUED,
    *,
    organization_id: uuid.UUID | None = None,
) -> ProcessingJob:
    return ProcessingJob(
        organization_id=organization_id or document.organization_id,
        document_id=document.id,
        job_type=JobType.INGEST,
        status=status,
    )


async def two_tenants(factory: Factory) -> tuple[Organization, KnowledgeBase, Organization]:
    """Org A with a knowledge base, and an unrelated org B."""
    org_a, org_b = new_org("acme"), new_org("globex")
    await add_all_and_commit(factory, org_a, org_b)
    kb_a = new_kb(org_a)
    await add_all_and_commit(factory, kb_a)
    return org_a, kb_a, org_b


async def count(factory: Factory, model: type[object]) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --- knowledge bases ------------------------------------------------------------------------


async def test_knowledge_base_server_defaults(session_factory: Factory) -> None:
    org = new_org()
    await add_all_and_commit(session_factory, org)
    kb = new_kb(org)
    await add_all_and_commit(session_factory, kb)

    assert (kb.chunk_size_tokens, kb.chunk_overlap_tokens) == (512, 64)
    assert kb.deleted_at is None


async def test_kb_names_are_unique_per_org_case_insensitively(session_factory: Factory) -> None:
    org_a, kb_a, org_b = await two_tenants(session_factory)

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_kb(org_a, name=kb_a.name.upper()))
    assert violated_constraint(exc_info.value) == "uq_knowledge_bases_org_name_active"

    # Another organization may use the same name.
    await add_all_and_commit(session_factory, new_kb(org_b, name=kb_a.name))


async def test_soft_deleted_kb_frees_its_name(session_factory: Factory) -> None:
    org_a, kb_a, _ = await two_tenants(session_factory)
    async with session_factory() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_a.id)
            .values(deleted_at=datetime.now(UTC))
        )
        await session.commit()

    await add_all_and_commit(session_factory, new_kb(org_a, name=kb_a.name))


@pytest.mark.parametrize(
    ("size", "overlap", "constraint"),
    [
        (512, 512, "ck_knowledge_bases_chunk_overlap_valid"),
        (512, -1, "ck_knowledge_bases_chunk_overlap_valid"),
        (32, 0, "ck_knowledge_bases_chunk_size_range"),
        (4096, 64, "ck_knowledge_bases_chunk_size_range"),
    ],
)
async def test_invalid_chunking_settings_are_rejected(
    session_factory: Factory, size: int, overlap: int, constraint: str
) -> None:
    org = new_org()
    await add_all_and_commit(session_factory, org)
    kb = new_kb(org)
    kb.chunk_size_tokens, kb.chunk_overlap_tokens = size, overlap

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, kb)
    assert violated_constraint(exc_info.value) == constraint


# --- documents: tenant integrity and de-duplication ----------------------------------------


async def test_document_cannot_belong_to_another_orgs_knowledge_base(
    session_factory: Factory,
) -> None:
    _, kb_a, org_b = await two_tenants(session_factory)

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_document(kb_a, organization_id=org_b.id))

    assert (
        violated_constraint(exc_info.value)
        == "fk_documents_knowledge_base_id_organization_id_knowledge_bases"
    )


async def test_same_file_twice_in_one_kb_is_rejected_but_allowed_in_another(
    session_factory: Factory,
) -> None:
    org_a, kb_a, _ = await two_tenants(session_factory)
    kb_a2 = new_kb(org_a, name="Policies")
    await add_all_and_commit(session_factory, kb_a2, new_document(kb_a, content="same"))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_document(kb_a, content="same"))
    assert violated_constraint(exc_info.value) == "uq_documents_kb_content_hash_active"

    await add_all_and_commit(session_factory, new_document(kb_a2, content="same"))


async def test_re_upload_is_allowed_while_the_old_copy_is_being_deleted(
    session_factory: Factory,
) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(
        session_factory, new_document(kb_a, content="same", status=DocumentStatus.DELETING)
    )

    await add_all_and_commit(session_factory, new_document(kb_a, content="same"))

    assert await count(session_factory, Document) == 2


async def test_concurrent_identical_uploads_exactly_one_wins(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)

    async def upload() -> str | None:
        """None on success, otherwise the name of the violated constraint."""
        try:
            await add_all_and_commit(session_factory, new_document(kb_a, content="race"))
        except IntegrityError as exc:
            return violated_constraint(exc) or "unknown"
        return None

    # Three transactions insert the same file at the same time. The partial unique index
    # makes the losers wait for the winner's commit, then fail; no application lock needed.
    outcomes = await asyncio.gather(upload(), upload(), upload())

    assert sorted(outcomes, key=str) == [
        None,
        "uq_documents_kb_content_hash_active",
        "uq_documents_kb_content_hash_active",
    ]
    assert await count(session_factory, Document) == 1


async def test_database_rejects_unknown_document_status(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    with pytest.raises(IntegrityError) as exc_info:
        async with session_factory() as session:
            await session.execute(
                text("UPDATE documents SET status = 'ARCHIVED' WHERE id = :id"), {"id": doc.id}
            )
    assert violated_constraint(exc_info.value) == "ck_documents_status"


# --- chunks ------------------------------------------------------------------------------------


async def test_chunks_are_idempotent_per_document_profile_and_index(
    session_factory: Factory,
) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))
    await add_all_and_commit(session_factory, new_chunk(doc, 0), new_chunk(doc, 1))

    # Re-inserting the same chunk (same deterministic id) is rejected, never duplicated.
    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_chunk(doc, 0))
    assert violated_constraint(exc_info.value) == "pk_document_chunks"

    # A second embedding profile (reindex in progress) coexists with the first.
    await add_all_and_commit(session_factory, new_chunk(doc, 0, profile="other-model__768__v1"))
    assert await count(session_factory, DocumentChunk) == 3


async def test_chunk_cannot_claim_another_tenant(session_factory: Factory) -> None:
    _, kb_a, org_b = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_chunk(doc, organization_id=org_b.id))
    assert (violated_constraint(exc_info.value) or "").startswith("fk_document_chunks_document_id")


async def test_chunk_cannot_claim_another_knowledge_base_of_the_same_org(
    session_factory: Factory,
) -> None:
    # The chunk's knowledge_base_id becomes the vector-search filter; a mismatch would make
    # the document appear in the wrong KB's search results.
    org_a, kb_a, _ = await two_tenants(session_factory)
    kb_a2 = new_kb(org_a, name="Policies")
    await add_all_and_commit(session_factory, kb_a2, doc := new_document(kb_a))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_chunk(doc, knowledge_base_id=kb_a2.id))
    assert (violated_constraint(exc_info.value) or "").startswith("fk_document_chunks_document_id")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"char_start": 50, "char_end": 50}, "ck_document_chunks_char_range_valid"),
        ({"page_start": 3, "page_end": 2}, "ck_document_chunks_page_range_valid"),
        ({"page_start": 0, "page_end": 1}, "ck_document_chunks_page_range_valid"),
    ],
)
async def test_chunk_ranges_are_validated(
    session_factory: Factory, overrides: dict[str, int], constraint: str
) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_chunk(doc, **overrides))  # type: ignore[arg-type]
    assert violated_constraint(exc_info.value) == constraint


async def test_chunks_without_pages_are_allowed(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    await add_all_and_commit(session_factory, new_chunk(doc, page_start=None, page_end=None))


async def test_chunk_without_explicit_id_is_rejected_by_orm_and_database(
    session_factory: Factory,
) -> None:
    """Chunk ids must come from chunk_id(); there is deliberately no fallback default."""
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    def chunk_without_id() -> DocumentChunk:
        chunk = new_chunk(doc)
        chunk.id = None  # type: ignore[assignment]
        return chunk

    # Layer 1: SQLAlchemy warns before sending SQL (an error under our warnings policy).
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        with pytest.raises(SAWarning, match="no Python-side or server-side default"):
            await add_all_and_commit(session_factory, chunk_without_id())

    # Layer 2: even with the warning ignored, PostgreSQL refuses a NULL primary key.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        with pytest.raises(IntegrityError) as exc_info:
            await add_all_and_commit(session_factory, chunk_without_id())
    assert 'null value in column "id"' in str(exc_info.value.orig)


# --- processing jobs -------------------------------------------------------------------------


async def test_only_one_active_job_per_document(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))
    await add_all_and_commit(session_factory, first := new_job(doc, JobStatus.RUNNING))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_job(doc, JobStatus.QUEUED))
    assert violated_constraint(exc_info.value) == "uq_processing_jobs_document_id_active"

    # Once the first job has finished, the document can be processed again.
    async with session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == first.id)
            .values(status=JobStatus.SUCCEEDED)
        )
        await session.commit()
    await add_all_and_commit(session_factory, new_job(doc, JobStatus.QUEUED))
    assert await count(session_factory, ProcessingJob) == 2


async def test_finished_jobs_do_not_block_each_other(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    await add_all_and_commit(
        session_factory,
        new_job(doc, JobStatus.FAILED),
        new_job(doc, JobStatus.CANCELLED),
        new_job(doc, JobStatus.SUCCEEDED),
        new_job(doc, JobStatus.RETRYING),
    )


async def test_job_cannot_claim_another_tenant(session_factory: Factory) -> None:
    _, kb_a, org_b = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_job(doc, organization_id=org_b.id))
    assert (
        violated_constraint(exc_info.value)
        == "fk_processing_jobs_document_id_organization_id_documents"
    )


async def test_job_defaults_attempt_limit_and_metrics(session_factory: Factory) -> None:
    _, kb_a, _ = await two_tenants(session_factory)
    await add_all_and_commit(session_factory, doc := new_document(kb_a))
    job = new_job(doc)
    job.stage = JobStage.EMBEDDING
    job.metrics = {"pages": 12, "chunks": 40, "stage_ms": {"extract": 812.5}}
    await add_all_and_commit(session_factory, job)

    async with session_factory() as session:
        stored = await session.get_one(ProcessingJob, job.id)
        assert (stored.attempts, stored.max_attempts) == (0, 5)
        assert stored.stage is JobStage.EMBEDDING
        assert stored.metrics == {"pages": 12, "chunks": 40, "stage_ms": {"extract": 812.5}}

    with pytest.raises(IntegrityError) as exc_info:
        async with session_factory() as session:
            await session.execute(
                update(ProcessingJob).where(ProcessingJob.id == job.id).values(attempts=6)
            )
    assert violated_constraint(exc_info.value) == "ck_processing_jobs_attempts_valid"


# --- deletes ---------------------------------------------------------------------------------


async def seeded_tree(factory: Factory) -> tuple[User, Organization, KnowledgeBase, Document]:
    user = new_user()
    await add_all_and_commit(factory, user)
    org = new_org(creator=user)
    await add_all_and_commit(factory, org)
    kb = new_kb(org)
    kb.created_by_user_id = user.id
    await add_all_and_commit(factory, kb)
    doc = new_document(kb)
    doc.uploaded_by_user_id = user.id
    await add_all_and_commit(factory, doc)
    await add_all_and_commit(factory, new_chunk(doc, 0), new_chunk(doc, 1), new_job(doc))
    return user, org, kb, doc


async def test_deleting_an_organization_removes_everything_it_owns(
    session_factory: Factory,
) -> None:
    _, org, _, _ = await seeded_tree(session_factory)

    async with session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == org.id))
        await session.commit()

    for model in (KnowledgeBase, Document, DocumentChunk, ProcessingJob):
        assert await count(session_factory, model) == 0, model.__name__


async def test_deleting_a_kb_leaves_sibling_kbs_untouched(session_factory: Factory) -> None:
    _, org, kb, _ = await seeded_tree(session_factory)
    sibling = new_kb(org, name="Policies")
    await add_all_and_commit(session_factory, sibling)
    await add_all_and_commit(session_factory, new_document(sibling, content="other"))

    async with session_factory() as session:
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
        await session.commit()

    assert await count(session_factory, KnowledgeBase) == 1
    assert await count(session_factory, Document) == 1
    assert await count(session_factory, DocumentChunk) == 0
    assert await count(session_factory, ProcessingJob) == 0


async def test_deleting_a_user_keeps_their_kbs_and_documents(session_factory: Factory) -> None:
    user, _, kb, doc = await seeded_tree(session_factory)

    async with session_factory() as session:
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()

    async with session_factory() as session:
        assert (await session.get_one(KnowledgeBase, kb.id)).created_by_user_id is None
        assert (await session.get_one(Document, doc.id)).uploaded_by_user_id is None
        assert await count(session_factory, DocumentChunk) == 2

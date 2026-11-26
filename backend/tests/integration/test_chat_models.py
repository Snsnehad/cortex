"""Conversations, messages, citations and usage events (TASK-02.05).

Runs on the scratch database built by the real migrations.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import (
    Conversation,
    Document,
    DocumentChunk,
    KnowledgeBase,
    Message,
    MessageCitation,
    MessageRole,
    MessageStatus,
    Organization,
    OrganizationMembership,
    OrgRole,
    UsageEvent,
    UsageEventType,
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


async def member_with_kb(
    factory: Factory, email: str = "alice@example.com", slug: str = "acme"
) -> tuple[User, Organization, KnowledgeBase]:
    user, org = new_user(email), new_org(slug)
    await add_all_and_commit(factory, user, org)
    kb = new_kb(org)
    await add_all_and_commit(
        factory,
        kb,
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=OrgRole.MEMBER),
    )
    return user, org, kb


def new_conversation(
    user: User, kb: KnowledgeBase, *, organization_id: uuid.UUID | None = None
) -> Conversation:
    return Conversation(
        organization_id=organization_id or kb.organization_id,
        knowledge_base_id=kb.id,
        user_id=user.id,
        title="What is the leave policy?",
    )


async def count(factory: Factory, model: type[object]) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def conversation_with_cited_answer(
    factory: Factory,
) -> tuple[User, Organization, KnowledgeBase, Conversation, Document, MessageCitation]:
    user, org, kb = await member_with_kb(factory)
    doc = new_document(kb)
    conversation = new_conversation(user, kb)
    await add_all_and_commit(factory, doc, conversation)
    chunk = DocumentChunk(
        id=chunk_id(doc.id, TEST_EMBEDDING_PROFILE, 0),
        organization_id=org.id,
        knowledge_base_id=kb.id,
        document_id=doc.id,
        embedding_profile=TEST_EMBEDDING_PROFILE,
        chunk_index=0,
        text="Employees accrue 1.5 days of leave per month.",
        token_count=10,
        char_start=0,
        char_end=45,
        page_start=12,
        page_end=12,
        content_hash=sha256_hex("leave"),
    )
    question = Message(
        conversation_id=conversation.id, role=MessageRole.USER, content="How much leave?"
    )
    answer = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Employees accrue 1.5 days per month [S1].",
        model="test-model",
        prompt_tokens=420,
        completion_tokens=18,
        total_latency_ms=950,
        retrieval_trace={"dense": [{"chunk": str(chunk.id), "score": 0.82}]},
    )
    await add_all_and_commit(factory, chunk, question, answer)
    citation = MessageCitation(
        message_id=answer.id,
        marker=1,
        chunk_id=chunk.id,
        document_id=doc.id,
        document_filename="handbook.pdf",
        page_start=12,
        page_end=12,
        snippet="Employees accrue 1.5 days of leave per month.",
        relevance_score=0.91,
    )
    await add_all_and_commit(factory, citation)
    return user, org, kb, conversation, doc, citation


# --- conversations: tenant and ownership integrity ----------------------------------------


async def test_conversation_cannot_use_another_orgs_knowledge_base(
    session_factory: Factory,
) -> None:
    user_a, _, kb_a = await member_with_kb(session_factory)
    _, org_b, _ = await member_with_kb(session_factory, "bob@example.com", "globex")
    # Alice is also a member of org B, so only the KB/tenant mismatch can fail.
    await add_all_and_commit(
        session_factory,
        OrganizationMembership(organization_id=org_b.id, user_id=user_a.id, role=OrgRole.MEMBER),
    )

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(
            session_factory, new_conversation(user_a, kb_a, organization_id=org_b.id)
        )
    assert (violated_constraint(exc_info.value) or "").startswith(
        "fk_conversations_knowledge_base_id_organization_id"
    )


async def test_only_members_can_own_a_conversation(session_factory: Factory) -> None:
    _, _, kb_a = await member_with_kb(session_factory)
    outsider = new_user("mallory@example.com")
    await add_all_and_commit(session_factory, outsider)

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, new_conversation(outsider, kb_a))
    assert (violated_constraint(exc_info.value) or "").startswith(
        "fk_conversations_organization_id_user_id"
    )


async def test_leaving_an_organization_deletes_your_conversations_there(
    session_factory: Factory,
) -> None:
    user, org, _, _, doc, _ = await conversation_with_cited_answer(session_factory)

    async with session_factory() as session:
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.user_id == user.id,
            )
        )
        await session.commit()

    for model in (Conversation, Message, MessageCitation):
        assert await count(session_factory, model) == 0, model.__name__
    # The organization's documents are untouched.
    async with session_factory() as session:
        assert await session.get(Document, doc.id) is not None


# --- messages and citations ------------------------------------------------------------------


async def test_history_loads_in_order_with_citations(session_factory: Factory) -> None:
    _, _, _, conversation, _, _ = await conversation_with_cited_answer(session_factory)

    async with session_factory() as session:
        loaded = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation.id)
            .options(selectinload(Conversation.messages).selectinload(Message.citations))
        )

    assert loaded is not None
    assert [m.role for m in loaded.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    answer = loaded.messages[1]
    assert answer.status is MessageStatus.COMPLETE  # server default
    assert answer.retrieval_trace is not None
    assert answer.retrieval_trace["dense"][0]["score"] == 0.82  # JSONB round trip
    assert [(c.marker, c.document_filename, c.page_start) for c in answer.citations] == [
        (1, "handbook.pdf", 12)
    ]


async def test_citation_survives_document_deletion_as_a_snapshot(
    session_factory: Factory,
) -> None:
    _, _, _, _, doc, citation = await conversation_with_cited_answer(session_factory)

    async with session_factory() as session:
        await session.execute(delete(Document).where(Document.id == doc.id))
        await session.commit()

    async with session_factory() as session:
        kept = await session.get_one(MessageCitation, citation.id)
    assert (kept.chunk_id, kept.document_id) == (None, None)
    assert (kept.document_filename, kept.page_start, kept.snippet) == (
        "handbook.pdf",
        12,
        "Employees accrue 1.5 days of leave per month.",
    )


async def test_citation_markers_are_unique_per_message_and_positive(
    session_factory: Factory,
) -> None:
    _, _, _, _, _, citation = await conversation_with_cited_answer(session_factory)

    def again(marker: int) -> MessageCitation:
        return MessageCitation(
            message_id=citation.message_id,
            marker=marker,
            document_filename="x.pdf",
            snippet="...",
        )

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, again(1))
    assert violated_constraint(exc_info.value) == "uq_message_citations_message_id_marker"

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, again(0))
    assert violated_constraint(exc_info.value) == "ck_message_citations_marker_positive"


async def test_deleting_a_conversation_removes_messages_and_citations(
    session_factory: Factory,
) -> None:
    _, _, _, conversation, _, _ = await conversation_with_cited_answer(session_factory)

    async with session_factory() as session:
        await session.delete(await session.get_one(Conversation, conversation.id))
        await session.commit()

    for model in (Conversation, Message, MessageCitation):
        assert await count(session_factory, model) == 0, model.__name__


@pytest.mark.parametrize(
    ("sql_fragment", "constraint"),
    [
        ("role = 'SYSTEM'", "ck_messages_role"),
        ("status = 'PENDING'", "ck_messages_status"),
        ("prompt_tokens = -1", "ck_messages_tokens_non_negative"),
        ("generation_latency_ms = -5", "ck_messages_latencies_non_negative"),
    ],
)
async def test_message_values_are_validated_by_the_database(
    session_factory: Factory, sql_fragment: str, constraint: str
) -> None:
    _, _, _, conversation, _, _ = await conversation_with_cited_answer(session_factory)

    with pytest.raises(IntegrityError) as exc_info:
        async with session_factory() as session:
            await session.execute(
                text(f"UPDATE messages SET {sql_fragment} WHERE conversation_id = :c"),  # noqa: S608
                {"c": conversation.id},
            )
    assert violated_constraint(exc_info.value) == constraint


# --- usage events ----------------------------------------------------------------------------


def usage(org: Organization, user: User | None, cost: str, **extra: object) -> UsageEvent:
    return UsageEvent(
        organization_id=org.id,
        user_id=user.id if user else None,
        event_type=UsageEventType.LLM_COMPLETION,
        provider="openai-compatible",
        model="test-model",
        input_tokens=420,
        output_tokens=18,
        cost_usd=Decimal(cost),
        **extra,
    )


async def test_costs_are_exact_decimals(session_factory: Factory) -> None:
    user, org, _ = await member_with_kb(session_factory)
    # Ten events of 0.1 USD. In binary floating point 0.1 is not exact: plain addition
    # drifts (0.1 + 0.2 != 0.3), and so would SQL sum() over a float column.
    await add_all_and_commit(session_factory, *(usage(org, user, "0.1") for _ in range(10)))
    await add_all_and_commit(session_factory, usage(org, user, "0.000123"))

    async with session_factory() as session:
        total = await session.scalar(select(func.sum(UsageEvent.cost_usd)))
    assert total == Decimal("1.000123")
    # The problem numeric avoids. (Python 3.12's sum() compensates for float error, so
    # demonstrate with plain addition.)
    assert 0.1 + 0.2 != 0.3


async def test_cost_is_stored_at_micro_dollar_precision(session_factory: Factory) -> None:
    user, org, _ = await member_with_kb(session_factory)
    event = usage(org, user, "0.0000004")  # 0.4 micro-dollars
    await add_all_and_commit(session_factory, event)

    async with session_factory() as session:
        stored = await session.scalar(select(UsageEvent.cost_usd).where(UsageEvent.id == event.id))
    assert stored == Decimal("0.000000")  # rounded to scale 6 by numeric(12,6)


async def test_negative_costs_and_unknown_event_types_are_rejected(
    session_factory: Factory,
) -> None:
    user, org, _ = await member_with_kb(session_factory)

    with pytest.raises(IntegrityError) as exc_info:
        await add_all_and_commit(session_factory, usage(org, user, "-0.01"))
    assert violated_constraint(exc_info.value) == "ck_usage_events_cost_non_negative"

    with pytest.raises(IntegrityError) as exc_info:
        async with session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO usage_events (id, organization_id, event_type, provider, model)"
                    " VALUES (:id, :org, 'IMAGE_GEN', 'p', 'm')"
                ),
                {"id": uuid.uuid4(), "org": org.id},
            )
    assert violated_constraint(exc_info.value) == "ck_usage_events_event_type"


async def test_usage_history_survives_deleting_what_caused_it(session_factory: Factory) -> None:
    user, org, _, conversation, doc, _ = await conversation_with_cited_answer(session_factory)
    await add_all_and_commit(
        session_factory, usage(org, user, "0.002", document_id=doc.id, message_id=uuid.uuid4())
    )

    async with session_factory() as session:
        await session.execute(delete(Document).where(Document.id == doc.id))
        await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()

    async with session_factory() as session:
        event = await session.scalar(select(UsageEvent))
    assert event is not None
    assert event.document_id == doc.id  # kept as a plain reference
    assert event.user_id is None  # ON DELETE SET NULL
    assert event.cost_usd == Decimal("0.002000")


async def test_usage_is_deleted_with_its_organization(session_factory: Factory) -> None:
    user, org, _ = await member_with_kb(session_factory)
    await add_all_and_commit(session_factory, usage(org, user, "0.5"))

    async with session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == org.id))
        await session.commit()

    assert await count(session_factory, UsageEvent) == 0

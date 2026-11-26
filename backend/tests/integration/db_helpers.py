"""Builders and assertions shared by database integration tests."""

import hashlib
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    Organization,
    User,
)

TEST_EMBEDDING_PROFILE = "bge-small-en-v1.5__384__v1"


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint PostgreSQL reported, proving *which* rule fired."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


async def add_all_and_commit(factory: async_sessionmaker[AsyncSession], *objs: object) -> None:
    async with factory() as session:
        session.add_all(objs)
        await session.commit()


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def new_user(email: str = "alice@example.com") -> User:
    return User(email=email, password_hash="argon2-hash-placeholder", full_name="Alice")


def new_org(slug: str = "acme", creator: User | None = None) -> Organization:
    return Organization(
        name=slug.title(), slug=slug, created_by_user_id=creator.id if creator else None
    )


def new_kb(org: Organization, name: str = "Handbook") -> KnowledgeBase:
    return KnowledgeBase(
        organization_id=org.id, name=name, embedding_profile=TEST_EMBEDDING_PROFILE
    )


def new_document(
    kb: KnowledgeBase,
    *,
    content: str = "file-bytes",
    status: DocumentStatus = DocumentStatus.PENDING,
    organization_id: uuid.UUID | None = None,
) -> Document:
    """A document in ``kb``. ``organization_id`` can be overridden to attempt a cross-tenant
    row; by default it is the KB's own organization."""
    document_id = uuid.uuid4()
    return Document(
        id=document_id,
        organization_id=organization_id or kb.organization_id,
        knowledge_base_id=kb.id,
        filename="handbook.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(content),
        content_hash=sha256_hex(content),
        storage_key=f"orgs/{kb.organization_id}/kbs/{kb.id}/docs/{document_id}/original",
        status=status,
    )

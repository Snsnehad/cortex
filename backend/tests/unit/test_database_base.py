"""Declarative base, naming convention and mixins (TASK-02.01)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database.base import (
    NAMING_CONVENTION,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class IsolatedBase(Base):
    """Test models live on their own MetaData so they never leak into Base.metadata
    (which Alembic autogenerate will read)."""

    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(UUIDPrimaryKeyMixin, TimestampMixin, IsolatedBase):
    __tablename__ = "widgets"

    name: Mapped[str] = mapped_column(String(50))


def ddl(*statements: CreateTable | CreateIndex) -> str:
    dialect = PGDialect()  # type: ignore[no-untyped-call]  # SQLAlchemy leaves it unannotated
    return "\n".join(str(s.compile(dialect=dialect)) for s in statements)


def test_naming_convention_produces_predictable_constraint_names() -> None:
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    Table("organizations", metadata, Column("id", Integer, primary_key=True))
    documents = Table(
        "documents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("organization_id", ForeignKey("organizations.id")),
        Column("content_hash", String(64)),
        Column("size", Integer),
        UniqueConstraint("organization_id", "content_hash"),
        CheckConstraint("size >= 0", name="size_non_negative"),
    )
    index = Index(None, documents.c.organization_id)

    sql = ddl(CreateTable(documents), CreateIndex(index))

    assert "CONSTRAINT pk_documents PRIMARY KEY" in sql
    assert "CONSTRAINT uq_documents_organization_id_content_hash UNIQUE" in sql
    assert "CONSTRAINT fk_documents_organization_id_organizations FOREIGN KEY" in sql
    assert "CONSTRAINT ck_documents_size_non_negative CHECK" in sql
    assert "CREATE INDEX ix_documents_organization_id" in sql


def test_mixins_map_to_native_uuid_and_timestamptz() -> None:
    table = Widget.__table__

    assert isinstance(table.c.id.type, Uuid)
    assert table.c.id.primary_key
    for column in (table.c.created_at, table.c.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert not column.nullable
        assert column.server_default is not None
    assert table.c.updated_at.onupdate is not None


def test_primary_key_default_generates_uuid7() -> None:
    default = Widget.__table__.c.id.default
    assert default is not None
    assert default.is_callable

    value = default.arg(None)  # SQLAlchemy wraps the callable to accept a context

    assert isinstance(value, uuid.UUID)
    assert value.version == 7


def test_server_values_are_fetched_eagerly() -> None:
    assert Widget.__mapper__.eager_defaults is True


def test_test_models_do_not_leak_into_the_application_metadata() -> None:
    assert "widgets" not in Base.metadata.tables
    assert Widget.metadata is IsolatedBase.metadata


def test_annotation_types_are_python_types() -> None:
    hints = Widget.__annotations__
    assert hints["name"] == Mapped[str]
    # Inherited from mixins
    assert UUIDPrimaryKeyMixin.__annotations__["id"] == Mapped[uuid.UUID]
    assert TimestampMixin.__annotations__["created_at"] == Mapped[datetime]

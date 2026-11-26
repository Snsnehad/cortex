"""Declarative base and shared column mixins for every ORM model.

Conventions (DATABASE_DESIGN.md §1):

* Constraint and index names are generated from a naming convention, so Alembic
  autogenerate produces stable, predictable names that migrations can reference
  (``pk_users``, ``uq_users_email``, ``fk_documents_knowledge_base_id_knowledge_bases`` …).
* ``uuid.UUID`` maps to PostgreSQL's native ``uuid``; ``datetime`` maps to ``timestamptz``.
  Naive datetimes are never stored.
* Primary keys are application-generated UUIDv7 (see ``app.utils.ids``).
* ``created_at`` / ``updated_at`` are set by the database (``now()``), so every writer
  (API, workers, manual SQL) agrees on the clock.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.utils.ids import uuid7

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: Uuid(as_uuid=True),
        datetime: DateTime(timezone=True),
    }

    # Fetch server-generated values (created_at, updated_at) with RETURNING as part of the
    # INSERT/UPDATE itself. Without this, SQLAlchemy expires them and reloads lazily on next
    # access, which raises MissingGreenlet under asyncio.
    # Typed `Any` to match SQLAlchemy's own declaration on its declarative base.
    __mapper_args__: Any = {"eager_defaults": True}  # noqa: RUF012


def _new_id() -> uuid.UUID:
    # Zero-argument on purpose: SQLAlchemy passes an execution context to column-default
    # callables that accept a positional parameter, and uuid7() has an optional one.
    return uuid7()


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_id)


class TimestampMixin:
    # now() is the transaction start time: all rows written in one transaction share it.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

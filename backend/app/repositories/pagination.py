"""Keyset pagination for SQLAlchemy queries.

Usage in a repository::

    order = KeysetOrder(Document.created_at, Document.id, descending=True)
    stmt = select(Document).where(Document.organization_id == org_id, ...)
    return await fetch_page(session, stmt, order, page=page, scope=scope, codec=codec)

``fetch_page`` adds ``WHERE (sort, id) < (:sort, :id)``, ``ORDER BY sort, id`` and
``LIMIT limit + 1``. The extra row tells us whether another page exists without a COUNT(*).
The sort column must be NOT NULL (row comparison with NULL never matches); ``id`` makes the
order total, so rows with identical timestamps (e.g. written in one transaction, which share
``now()``) are neither duplicated nor skipped.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Cursor,
    CursorCodec,
    PageResult,
    SortValue,
)


@dataclass(frozen=True, slots=True)
class PageRequest:
    """What the client asked for: page size and (optionally) where the last page ended."""

    limit: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True)
class KeysetOrder:
    sort_column: InstrumentedAttribute[Any]
    id_column: InstrumentedAttribute[uuid.UUID]
    descending: bool = True

    def key_of(self, row: object) -> tuple[SortValue, uuid.UUID]:
        return (
            cast(SortValue, getattr(row, self.sort_column.key)),
            cast(uuid.UUID, getattr(row, self.id_column.key)),
        )


def apply_keyset(
    stmt: Select[Any], order: KeysetOrder, *, after: Cursor | None, limit: int
) -> Select[Any]:
    sort_col, id_col = order.sort_column, order.id_column
    if after is not None:
        # Row-value comparison: (sort, id) < (cursor_sort, cursor_id). Bound with the
        # columns' own types so timestamps/uuids compare natively in PostgreSQL.
        boundary = tuple_(
            literal(after.sort_value, type_=sort_col.type),
            literal(after.id, type_=id_col.type),
        )
        position = tuple_(sort_col, id_col)
        stmt = stmt.where(position < boundary if order.descending else position > boundary)
    if order.descending:
        stmt = stmt.order_by(sort_col.desc(), id_col.desc())
    else:
        stmt = stmt.order_by(sort_col.asc(), id_col.asc())
    return stmt.limit(limit + 1)


def build_page[T](
    rows: list[T],
    *,
    limit: int,
    scope: str,
    codec: CursorCodec,
    key: Callable[[T], tuple[SortValue, uuid.UUID]],
) -> PageResult[T]:
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more:
        sort_value, row_id = key(items[-1])
        next_cursor = codec.encode(Cursor(scope=scope, sort_value=sort_value, id=row_id))
    return PageResult(items=items, next_cursor=next_cursor, has_more=has_more)


async def fetch_page(
    session: AsyncSession,
    stmt: Select[Any],
    order: KeysetOrder,
    *,
    page: PageRequest,
    scope: str,
    codec: CursorCodec,
) -> PageResult[Any]:
    """Run ``stmt`` as one keyset page. ``stmt`` must select a single ORM entity and already
    contain every filter (tenant, knowledge base, status ...)."""
    after = codec.decode(page.cursor, scope=scope) if page.cursor else None
    result = await session.scalars(apply_keyset(stmt, order, after=after, limit=page.limit))
    return build_page(
        list(result.all()), limit=page.limit, scope=scope, codec=codec, key=order.key_of
    )

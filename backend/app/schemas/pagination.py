"""List response envelope (API_SPECIFICATION.md §1)."""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.core.pagination import PageResult


class Page[ItemT](BaseModel):
    items: list[ItemT]
    next_cursor: str | None = Field(
        default=None, description="Pass as ?cursor= to get the next page; null on the last page."
    )
    has_more: bool

    @classmethod
    def from_result(cls, result: PageResult[Any], convert: Callable[[Any], ItemT]) -> "Page[ItemT]":
        return cls(
            items=[convert(item) for item in result.items],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

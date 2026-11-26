"""Pagination dependencies for list endpoints.

@router.get("/orgs/{org_id}/knowledge-bases/{kb_id}/documents", response_model=Page[DocumentOut])
async def list_documents(page: PageParams, codec: CursorCodecDep, ...): ...
"""

from typing import Annotated

from fastapi import Depends, Query, Request

from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, CursorCodec
from app.repositories.pagination import PageRequest


def get_page_request(
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page.")
    ] = DEFAULT_PAGE_SIZE,
    # No max_length here on purpose: every cursor problem (too long, tampered, wrong listing)
    # gets the same 400 INVALID_CURSOR from the codec.
    cursor: Annotated[
        str | None, Query(description="Opaque cursor from the previous page's next_cursor.")
    ] = None,
) -> PageRequest:
    return PageRequest(limit=limit, cursor=cursor)


def get_cursor_codec(request: Request) -> CursorCodec:
    codec: CursorCodec = request.app.state.cursor_codec
    return codec


PageParams = Annotated[PageRequest, Depends(get_page_request)]
CursorCodecDep = Annotated[CursorCodec, Depends(get_cursor_codec)]

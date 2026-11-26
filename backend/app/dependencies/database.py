"""Request-scoped database session dependency."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; always closed, rolled back on error.

    Closing an AsyncSession rolls back any transaction that was not committed and returns the
    connection to the pool, so a failing request can neither leak a connection nor leave
    half-written data behind.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db_session)]

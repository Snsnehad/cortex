"""File storage as a FastAPI dependency (built once per app: ``app.state.storage``)."""

from typing import Annotated

from fastapi import Depends, Request

from app.storage import StorageBackend


def get_storage(request: Request) -> StorageBackend:
    storage: StorageBackend = request.app.state.storage
    return storage


StorageDep = Annotated[StorageBackend, Depends(get_storage)]

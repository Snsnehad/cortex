"""Object storage abstraction with local-filesystem and S3 backends.

Use ``create_storage(settings.storage)`` once per process (the API keeps it on
``app.state.storage``; routes get it through ``StorageDep``). Code depends only on the
``StorageBackend`` protocol, never on a concrete backend.
"""

from app.config.settings import StorageSettings
from app.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageBackend,
    StorageError,
    StorageUnavailableError,
    StoredObject,
    document_key,
    read_all,
    validate_key,
)

__all__ = [
    "InvalidStorageKeyError",
    "ObjectNotFoundError",
    "StorageBackend",
    "StorageError",
    "StorageUnavailableError",
    "StoredObject",
    "create_storage",
    "document_key",
    "read_all",
    "validate_key",
]


def create_storage(settings: StorageSettings) -> StorageBackend:
    if settings.backend == "s3":
        from app.storage.s3 import S3Storage, create_s3_client  # boto3 only when needed

        if not settings.s3_bucket:  # also enforced by StorageSettings validation
            raise ValueError("storage.s3_bucket is required when storage.backend is 's3'")
        return S3Storage(
            create_s3_client(settings),
            bucket=settings.s3_bucket,
            sse=settings.s3_sse,
            kms_key_id=settings.s3_kms_key_id,
        )
    from app.storage.local import LocalStorage

    return LocalStorage(settings.local_root)

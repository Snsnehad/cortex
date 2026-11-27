"""Amazon S3 storage (staging and production; also S3-compatible services in testing).

* **Private by design.** Objects are never public; reads go through the API or through a
  short-lived presigned GET whose response is forced to ``Content-Disposition: attachment``,
  so an uploaded HTML/SVG file cannot run in a browser from the bucket's origin.
* **Encrypted at rest**: every ``put`` asks for server-side encryption (``AES256`` or
  ``aws:kms``), instead of relying only on the bucket default.
* **Credentials** come from boto3's standard chain (environment, shared config, instance or
  task role). Cortex settings never hold AWS keys.
* **Streaming upload with a size limit**: the content is spooled (memory up to 8 MiB, then a
  temporary file) while it is hashed and measured, and is sent only once it is known to be
  within ``max_bytes``. Nothing oversized ever reaches the bucket. boto3 switches to a
  multipart upload for large files.
* boto3 is synchronous; every call runs in a worker thread.

Failures: a missing key raises ``ObjectNotFoundError``. Network errors, timeouts and S3 5xx
or throttling raise ``StorageUnavailableError`` (503, retryable). Anything else (access
denied, missing bucket) is a configuration error and propagates as a 500.
"""

import tempfile
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, TypeVar

import boto3
from anyio import to_thread
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.config.settings import StorageSettings
from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ContentMeter,
    ObjectNotFoundError,
    StorageUnavailableError,
    StoredObject,
    as_chunks,
    content_disposition,
    validate_key,
)

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

T = TypeVar("T")

SPOOL_MEMORY_BYTES = 8 * 1024 * 1024
_NETWORK_ERRORS = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ConnectionClosedError,
)
_RETRYABLE_CODES = frozenset(
    {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout", "Throttling"}
)
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NotFound", "404"})


def create_s3_client(settings: StorageSettings) -> "S3Client":
    config = Config(
        signature_version="s3v4",
        retries={"mode": "standard", "max_attempts": 3},
        connect_timeout=5,
        read_timeout=30,
        # S3-compatible test servers are addressed by path; AWS by virtual host.
        s3={"addressing_style": "path" if settings.s3_endpoint_url else "virtual"},
    )
    client: S3Client = boto3.client(
        "s3",
        region_name=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
        config=config,
    )
    return client


class S3Storage:
    name = "s3"

    def __init__(
        self,
        client: "S3Client",
        *,
        bucket: str,
        sse: str = "AES256",
        kms_key_id: str | None = None,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._encryption: dict[str, str] = {"ServerSideEncryption": sse}
        if kms_key_id:
            self._encryption["SSEKMSKeyId"] = kms_key_id

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterable[bytes],
        *,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StoredObject:
        validate_key(key)
        meter = ContentMeter(max_bytes)
        spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)  # noqa: SIM115
        try:
            async with aclosing(as_chunks(data)) as chunks:
                async for chunk in chunks:
                    meter.update(chunk)  # raises before anything is uploaded
                    await to_thread.run_sync(spool.write, chunk)
            stored = meter.result(key)
            await to_thread.run_sync(spool.seek, 0)
            extra: dict[str, Any] = {
                "ContentType": content_type,
                "Metadata": {"sha256": stored.sha256},
                **self._encryption,
            }
            await self._call(
                lambda: self._client.upload_fileobj(spool, self._bucket, key, ExtraArgs=extra)
            )
        finally:
            await to_thread.run_sync(spool.close)
        return stored

    async def iter_chunks(
        self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> AsyncGenerator[bytes]:
        validate_key(key)
        try:
            response = await self._call(
                lambda: self._client.get_object(Bucket=self._bucket, Key=key)
            )
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(key) from None
            raise
        body = response["Body"]
        try:
            while chunk := await self._call(lambda: body.read(chunk_size)):
                yield chunk
        finally:
            await to_thread.run_sync(body.close)

    async def exists(self, key: str) -> bool:
        validate_key(key)
        try:
            await self._call(lambda: self._client.head_object(Bucket=self._bucket, Key=key))
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        validate_key(key)
        # S3 DeleteObject succeeds for missing keys, so this is idempotent as required.
        await self._call(lambda: self._client.delete_object(Bucket=self._bucket, Key=key))

    async def aclose(self) -> None:
        """Close the client's HTTP connection pool (on application shutdown)."""
        await to_thread.run_sync(self._client.close)

    async def presigned_get_url(
        self, key: str, *, filename: str, expires_in_seconds: int
    ) -> str | None:
        validate_key(key)
        # Signing is local (no network call), so no thread is needed.
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": content_disposition(filename),
            },
            ExpiresIn=expires_in_seconds,
        )

    async def _call(self, func: Callable[[], T]) -> T:
        try:
            return await to_thread.run_sync(func)
        except _NETWORK_ERRORS as exc:
            raise StorageUnavailableError from exc
        except ClientError as exc:
            if _is_retryable(exc):
                raise StorageUnavailableError from exc
            raise


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _is_retryable(exc: ClientError) -> bool:
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
    return status >= 500 or _error_code(exc) in _RETRYABLE_CODES

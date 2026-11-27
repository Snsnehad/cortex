"""The storage contract shared by every backend (local filesystem, S3).

Keys
----
Object keys are built **only from server-side ids** (``document_key``), never from a user's
filename, and every backend validates them with ``validate_key`` before use: lowercase-or-
uppercase ASCII letters, digits, ``.``, ``_``, ``-`` in ``/``-separated segments. A segment
must start with a letter or digit and must not end with a dot, so ``..``, hidden files, empty
segments, absolute paths, backslashes, drive letters (``C:``) and Windows' silently stripped
trailing dots are all impossible. The local backend adds its own checks on top (see
``LocalStorage``); validation here is the first of two independent walls.

Writes
------
``put`` streams the content once, computing its SHA-256 and enforcing ``max_bytes`` on the
way. Going over the limit raises ``PayloadTooLargeError`` and leaves **no object behind**. A
put replaces any existing object at the key.

Errors
------
* ``ObjectNotFoundError``: reading a key that does not exist.
* ``InvalidStorageKeyError``: a key that fails validation (a bug in the caller, never user
  input, since keys come from ids).
* ``StorageUnavailableError`` (503): the store could not be reached; the caller may retry.
"""

import hashlib
import re
import unicodedata
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from app.core.errors import PayloadTooLargeError, UpstreamUnavailableError

MAX_KEY_LENGTH = 500  # documents.storage_key is varchar(500)
DEFAULT_CHUNK_SIZE = 1024 * 1024
_SEGMENT = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?"
_VALID_KEY = re.compile(rf"^{_SEGMENT}(?:/{_SEGMENT})*$")


class StorageError(Exception):
    """Base class for storage failures that are not the client's fault."""


class ObjectNotFoundError(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"no object at key {key!r}")
        self.key = key


class InvalidStorageKeyError(StorageError, ValueError):
    pass


class StorageUnavailableError(UpstreamUnavailableError):
    default_code = "STORAGE_UNAVAILABLE"
    default_message = "File storage is temporarily unavailable. Please retry."


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str  # lowercase hex digest of the stored content


class StorageBackend(Protocol):
    """What the rest of the application may do with stored files."""

    name: str

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterable[bytes],
        *,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    def iter_chunks(
        self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> AsyncGenerator[bytes]:
        """Stream an object. Raises ``ObjectNotFoundError`` on first iteration if missing.

        The stream holds an open file or HTTP response until it is exhausted. A caller that
        may stop early must close it: ``async with aclosing(storage.iter_chunks(key)) as s``.
        """
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove an object; deleting a missing key is not an error (idempotent)."""
        ...

    async def presigned_get_url(
        self, key: str, *, filename: str, expires_in_seconds: int
    ) -> str | None:
        """A time-limited direct download URL, or None if the backend cannot provide one
        (the API then streams the file itself)."""
        ...


def validate_key(key: str) -> str:
    if len(key) > MAX_KEY_LENGTH or not _VALID_KEY.fullmatch(key):
        raise InvalidStorageKeyError(f"invalid storage key: {key!r}")
    return key


def document_key(
    organization_id: uuid.UUID, knowledge_base_id: uuid.UUID, document_id: uuid.UUID
) -> str:
    """Where a document's original file lives. Ids only: never a user-supplied name."""
    return validate_key(
        f"orgs/{organization_id}/kbs/{knowledge_base_id}/docs/{document_id}/original"
    )


async def read_all(storage: StorageBackend, key: str, *, max_bytes: int | None = None) -> bytes:
    """Read a whole object into memory (for small files; ``max_bytes`` guards the size)."""
    parts: list[bytes] = []
    total = 0
    # aclosing: stopping early (over the limit) must still close the file / HTTP response
    # now, not whenever the garbage collector gets to it.
    async with aclosing(storage.iter_chunks(key)) as chunks:
        async for chunk in chunks:
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise PayloadTooLargeError(details={"max_bytes": max_bytes})
            parts.append(chunk)
    return b"".join(parts)


class ContentMeter:
    """Hashes and counts content as it streams past; raises once it exceeds ``max_bytes``."""

    def __init__(self, max_bytes: int | None) -> None:
        self._max_bytes = max_bytes
        self._hash = hashlib.sha256()
        self.size_bytes = 0

    def update(self, chunk: bytes) -> None:
        self.size_bytes += len(chunk)
        if self._max_bytes is not None and self.size_bytes > self._max_bytes:
            raise PayloadTooLargeError(
                f"The file is larger than the limit of {self._max_bytes} bytes.",
                details={"max_bytes": self._max_bytes},
            )
        self._hash.update(chunk)

    def result(self, key: str) -> StoredObject:
        return StoredObject(key=key, size_bytes=self.size_bytes, sha256=self._hash.hexdigest())


async def as_chunks(data: bytes | AsyncIterable[bytes]) -> AsyncGenerator[bytes]:
    """Uniform chunk stream for ``put``. Closing it also closes the source stream, so an
    upload abandoned part-way (size limit, error) releases the request body at once."""
    if isinstance(data, bytes):
        if data:
            yield data
        return
    try:
        async for chunk in data:
            if chunk:
                yield chunk
    finally:
        close = getattr(data, "aclose", None)
        if close is not None:
            await close()


def content_disposition(filename: str) -> str:
    """``attachment`` header value for a user-supplied filename (RFC 6266 / RFC 5987).

    Always ``attachment``: a stored HTML or SVG file must never render in the browser. The
    plain ``filename`` is an ASCII fallback with quotes, backslashes and control characters
    removed; ``filename*`` carries the exact UTF-8 name, percent-encoded.
    """
    cleaned = (
        "".join(
            ch
            for ch in unicodedata.normalize("NFC", filename)
            if unicodedata.category(ch)[0] != "C"
        ).strip()
        or "download"
    )
    ascii_fallback = (
        unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    )
    ascii_fallback = re.sub(r'["\\]', "_", ascii_fallback).strip() or "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"

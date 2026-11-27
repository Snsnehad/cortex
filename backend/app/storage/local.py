"""Local-filesystem storage (development and tests; staging/production require S3).

Confinement to ``root`` has two independent walls:

1. ``validate_key`` (shared with S3) rejects ``..``, absolute paths, backslashes, drive
   letters and trailing dots.
2. Here, the final path is **resolved** (following any symlinks) and must still lie inside
   the resolved root. A symlink planted inside the root that points elsewhere is therefore
   refused, as are Windows device names (``CON``, ``NUL``, ``COM1`` …) that would address a
   device instead of a file.

Writes go to a temporary file in ``<root>/.tmp/``, are flushed to disk, then renamed over
the destination (``os.replace`` is atomic within one filesystem). The temporary name is short
on purpose: document keys are ~130 characters, and Windows refuses paths over 260 (a long
``<dir>/.original.<uuid>.tmp`` next to the destination exceeded it). ``.tmp`` can never
collide with a key, because key segments cannot start with a dot. A reader never
sees a half-written file, and a failed or oversized upload leaves nothing behind.

Blocking file I/O runs in worker threads, so the event loop keeps serving other requests.
"""

import contextlib
import os
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import aclosing
from pathlib import Path
from typing import BinaryIO

from anyio import to_thread

from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ContentMeter,
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StoredObject,
    as_chunks,
    validate_key,
)

TEMP_DIR_NAME = ".tmp"
_WINDOWS_DEVICE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)
        self._temp_dir = self._root / TEMP_DIR_NAME
        self._temp_dir.mkdir(exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, key: str) -> Path:
        validate_key(key)
        if any(_WINDOWS_DEVICE.match(segment) for segment in key.split("/")):
            raise InvalidStorageKeyError(f"invalid storage key: {key!r}")
        path = (self._root / key).resolve(strict=False)
        if path == self._root or not path.is_relative_to(self._root):
            raise InvalidStorageKeyError(f"storage key escapes the storage root: {key!r}")
        return path

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterable[bytes],
        *,
        content_type: str,  # not stored: the database keeps the detected type
        max_bytes: int | None = None,
    ) -> StoredObject:
        path = self.path_for(key)
        await to_thread.run_sync(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        temp = self._temp_dir / f"{uuid.uuid4().hex}.tmp"
        meter = ContentMeter(max_bytes)
        handle: BinaryIO = await to_thread.run_sync(temp.open, "xb")
        try:
            async with aclosing(as_chunks(data)) as chunks:
                async for chunk in chunks:
                    meter.update(chunk)  # raises before an oversized chunk is written
                    await to_thread.run_sync(handle.write, chunk)
            await to_thread.run_sync(_flush_and_close, handle)
            # The path is re-checked right before the rename: a symlink swapped into the
            # directory while we were writing must not redirect the final file.
            if self.path_for(key) != path:
                raise InvalidStorageKeyError(f"storage key escapes the storage root: {key!r}")
            await to_thread.run_sync(os.replace, temp, path)
        except BaseException:
            await to_thread.run_sync(_discard, handle, temp)
            raise
        return meter.result(key)

    async def iter_chunks(
        self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> AsyncGenerator[bytes]:
        path = self.path_for(key)
        try:
            handle: BinaryIO = await to_thread.run_sync(path.open, "rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise ObjectNotFoundError(key) from None
        try:
            while chunk := await to_thread.run_sync(handle.read, chunk_size):
                yield chunk
        finally:
            await to_thread.run_sync(handle.close)

    async def exists(self, key: str) -> bool:
        path = self.path_for(key)
        return await to_thread.run_sync(path.is_file)

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        await to_thread.run_sync(lambda: path.unlink(missing_ok=True))

    async def presigned_get_url(
        self, key: str, *, filename: str, expires_in_seconds: int
    ) -> str | None:
        self.path_for(key)  # same validation as every other operation
        return None  # no direct URLs: the API streams local files itself


def _flush_and_close(handle: BinaryIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()


def _discard(handle: BinaryIO, temp: Path) -> None:
    with contextlib.suppress(OSError):
        handle.close()
    temp.unlink(missing_ok=True)

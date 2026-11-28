"""Reading archive entries with a hard cap on the bytes *actually* decompressed (TASK-06.02).

Upload intake (ADR-023) refuses archives by their *declared* sizes, which cost nothing to
check but can lie. Here every byte that comes out of the decompressor is counted, so a
"zip bomb" (a few kilobytes that inflate to gigabytes) is stopped after at most ``limit``
bytes, however its directory describes it. Memory stays bounded too: callers stream from
the reader instead of loading the entry.
"""

import zipfile
from typing import IO

from app.rag.ingestion.parsed import ExtractionError

ARCHIVE_TOO_LARGE = "ARCHIVE_TOO_LARGE"
_ENCRYPTED_FLAG = 0x1


class LimitedReader:
    """A file-like view of one archive entry that refuses to yield more than ``limit``
    decompressed bytes."""

    def __init__(self, raw: IO[bytes], limit: int) -> None:
        self._raw = raw
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        # Never ask for more than one byte past the limit: that byte proves it is exceeded.
        allowed = self._limit - self.bytes_read + 1
        chunk = self._raw.read(allowed if size < 0 else min(size, allowed))
        self.bytes_read += len(chunk)
        if self.bytes_read > self._limit:
            raise ExtractionError(
                ARCHIVE_TOO_LARGE,
                "The document expands to more data than can be processed.",
            )
        return chunk

    def close(self) -> None:
        self._raw.close()


def open_entry(archive: zipfile.ZipFile, name: str, *, limit: int) -> LimitedReader:
    """Open ``name`` for streaming with a decompressed-size cap. Encrypted entries are
    refused (the caller reports the archive as invalid)."""
    info = archive.getinfo(name)  # KeyError if missing
    if info.flag_bits & _ENCRYPTED_FLAG:
        raise zipfile.BadZipFile(f"encrypted entry: {name}")
    return LimitedReader(archive.open(info), limit)

"""Upload intake: receive a multipart file as a stream, then decide what it really is.

Typical use in a route (TASK-04.05)::

    upload = await receive_upload(request, max_bytes=settings.storage.max_upload_bytes)
    try:
        detected = await detect_upload(upload)   # 415 / 422 here
        ...store upload.file, record upload.sha256 / size / detected.mime_type...
    finally:
        upload.close()
"""

from anyio import to_thread

from app.uploads.detect import (
    DetectedType,
    EmptyFileError,
    UnsupportedFileTypeError,
    detect_file_type,
)
from app.uploads.filenames import sanitize_filename
from app.uploads.receive import (
    FileTooLargeError,
    InvalidUploadError,
    ReceivedFile,
    receive_upload,
)

__all__ = [
    "DetectedType",
    "EmptyFileError",
    "FileTooLargeError",
    "InvalidUploadError",
    "ReceivedFile",
    "UnsupportedFileTypeError",
    "detect_file_type",
    "detect_upload",
    "receive_upload",
    "sanitize_filename",
]


async def detect_upload(upload: ReceivedFile) -> DetectedType:
    """``detect_file_type`` in a worker thread (it reads the whole file for text checks)."""
    return await to_thread.run_sync(detect_file_type, upload.file, upload.filename)

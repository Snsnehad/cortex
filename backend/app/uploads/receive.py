"""Receive a ``multipart/form-data`` file upload as a stream, with limits enforced on arrival.

FastAPI's ``UploadFile`` reads the **whole** request body into a temporary file before the
route runs, so a client could make the server receive gigabytes before a size check. Here
the body is fed chunk by chunk into python-multipart's push parser:

* a declared ``Content-Length`` that is already too big → 413 before reading anything;
* the file part is counted (and hashed) as it arrives; one byte over ``max_bytes`` → 413
  ``FILE_TOO_LARGE`` at once, whatever the headers claimed;
* the whole body is capped too (file limit + a small allowance for multipart framing), which
  also covers ``Transfer-Encoding: chunked`` bodies without a length;
* exactly one part, named ``file``, carrying a filename. Anything else, including a second
  file or a form field, is 400 ``INVALID_UPLOAD``; malformed multipart is 400 as well.

The content is spooled (memory up to 1 MiB, then a temporary file) for type detection
(``app.uploads.detect``) and then storage. The caller must ``close()`` the result.
"""

import hashlib
import tempfile
from dataclasses import dataclass, field
from typing import IO

from anyio import to_thread
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from app.core.errors import BadRequestError, PayloadTooLargeError
from app.uploads.filenames import sanitize_filename

FILE_FIELD = "file"
MULTIPART_OVERHEAD_BYTES = 64 * 1024  # boundaries and part headers on top of the file
SPOOL_MEMORY_BYTES = 1024 * 1024


class FileTooLargeError(PayloadTooLargeError):
    default_code = "FILE_TOO_LARGE"
    default_message = "The file is too large."


class InvalidUploadError(BadRequestError):
    default_code = "INVALID_UPLOAD"
    default_message = "Send the file as multipart/form-data in a single field named 'file'."


@dataclass
class ReceivedFile:
    filename: str  # sanitized display name
    declared_content_type: str | None  # what the client claimed; informational only
    size_bytes: int
    sha256: str
    file: IO[bytes]  # positioned at the start

    def close(self) -> None:
        self.file.close()


@dataclass
class _ParseState:
    max_bytes: int
    header_field: bytearray = field(default_factory=bytearray)
    header_value: bytearray = field(default_factory=bytearray)
    headers: dict[bytes, bytes] = field(default_factory=dict)
    in_file: bool = False
    file_complete: bool = False
    filename: str | None = None
    declared_content_type: str | None = None
    size_bytes: int = 0
    digest: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    pending: list[bytes] = field(default_factory=list)  # file bytes not yet written
    finished: bool = False

    # --- python-multipart callbacks (synchronous; they only collect and check) ---

    def on_part_begin(self) -> None:
        self.headers = {}

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_value += data[start:end]

    def on_header_end(self) -> None:
        self.headers[bytes(self.header_field).lower()] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()

    def on_headers_finished(self) -> None:
        disposition, params = parse_options_header(self.headers.get(b"content-disposition"))
        if disposition != b"form-data" or params.get(b"name") != FILE_FIELD.encode():
            raise InvalidUploadError(
                "Unexpected form field: send only a single field named 'file'."
            )
        if self.filename is not None:
            raise InvalidUploadError("Send exactly one file per request.")
        if b"filename" not in params:
            raise InvalidUploadError("The 'file' field must be a file, not a text value.")
        self.filename = sanitize_filename(params[b"filename"].decode("utf-8", "replace"))
        content_type = self.headers.get(b"content-type")
        self.declared_content_type = content_type.decode("latin-1")[:100] if content_type else None
        self.in_file = True

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if not self.in_file:
            return
        piece = data[start:end]
        self.size_bytes += len(piece)
        if self.size_bytes > self.max_bytes:
            raise _too_large(self.max_bytes)
        self.digest.update(piece)
        self.pending.append(piece)

    def on_part_end(self) -> None:
        if self.in_file:
            self.in_file = False
            self.file_complete = True

    def on_end(self) -> None:
        self.finished = True


def _too_large(max_bytes: int) -> FileTooLargeError:
    return FileTooLargeError(
        f"The file is larger than the limit of {max_bytes} bytes.",
        details={"max_bytes": max_bytes},
    )


def _boundary(request: Request) -> bytes:
    content_type, params = parse_options_header(request.headers.get("content-type"))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise InvalidUploadError("The request must be multipart/form-data.")
    return boundary


def _check_declared_length(request: Request, body_limit: int, max_bytes: int) -> None:
    declared = request.headers.get("content-length")
    if declared is None:
        return
    if not declared.isdigit():
        raise InvalidUploadError("Invalid Content-Length header.")
    if int(declared) > body_limit:
        raise _too_large(max_bytes)


async def receive_upload(request: Request, *, max_bytes: int) -> ReceivedFile:
    """Stream the upload into a spool while checking limits; see the module docstring."""
    boundary = _boundary(request)
    body_limit = max_bytes + MULTIPART_OVERHEAD_BYTES
    _check_declared_length(request, body_limit, max_bytes)

    state = _ParseState(max_bytes=max_bytes)
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": state.on_part_begin,
            "on_header_field": state.on_header_field,
            "on_header_value": state.on_header_value,
            "on_header_end": state.on_header_end,
            "on_headers_finished": state.on_headers_finished,
            "on_part_data": state.on_part_data,
            "on_part_end": state.on_part_end,
            "on_end": state.on_end,
        },
    )
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)  # noqa: SIM115
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > body_limit:
                raise _too_large(max_bytes)
            try:
                parser.write(chunk)
            except FormParserError as exc:
                raise InvalidUploadError("Malformed multipart body.") from exc
            for piece in state.pending:
                await to_thread.run_sync(spool.write, piece)
            state.pending.clear()
        try:
            parser.finalize()
        except FormParserError as exc:
            raise InvalidUploadError("Malformed multipart body.") from exc
        if state.filename is None:
            raise InvalidUploadError("No file was sent: use a field named 'file'.")
        if not (state.file_complete and state.finished):
            raise InvalidUploadError("The upload ended before the file was complete.")
        await to_thread.run_sync(spool.seek, 0)
    except BaseException:
        await to_thread.run_sync(spool.close)
        raise
    return ReceivedFile(
        filename=state.filename,
        declared_content_type=state.declared_content_type,
        size_bytes=state.size_bytes,
        sha256=state.digest.hexdigest(),
        file=spool,
    )

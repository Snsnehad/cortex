"""What an uploaded file really is, decided from its bytes.

The client's ``Content-Type`` and the filename extension are never trusted: a renamed
executable is still an executable. Accepted types (API_SPECIFICATION.md):

* **PDF** (``application/pdf``): starts with ``%PDF-1.x`` or ``%PDF-2.x``.
* **DOCX** (``application/vnd.openxmlformats-officedocument.wordprocessingml.document``): a
  ZIP whose ``[Content_Types].xml`` declares the WordprocessingML main document and which
  contains ``word/document.xml``; no macros, no encrypted entries, within zip-bomb limits.
* **Text** (``text/plain``; ``text/markdown`` when the name ends in ``.md``/``.markdown``):
  valid UTF-8 (BOM allowed), no NUL and no control characters other than tab, newline,
  carriage return and form feed.

Everything else is 415 ``UNSUPPORTED_FILE_TYPE``; an empty file is 422 ``FILE_EMPTY``.

Macro-enabled Word files (``.docm``) and templates declare a different main content type,
so they are refused; a ``vbaProject.bin`` anywhere is refused as well. The ZIP limits use
the sizes *declared* in the archive's directory, which cost nothing to read. Declared sizes
can lie, so the DOCX parser (Phase 6) must also cap the bytes it actually decompresses.

Detection is synchronous file I/O: call it from a worker thread in async code.
"""

import codecs
import re
import zipfile
from dataclasses import dataclass
from typing import IO

from app.core.errors import UnsupportedMediaTypeError, ValidationFailedError
from app.uploads.filenames import extension_of

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
MARKDOWN_MIME = "text/markdown"
MARKDOWN_EXTENSIONS = frozenset({"md", "markdown"})

DOCX_MAIN_CONTENT_TYPE = (
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
DOCX_MAX_ENTRIES = 5_000
DOCX_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
DOCX_MAX_CONTENT_TYPES_BYTES = 1024 * 1024
_ZIP_ENCRYPTED_FLAG = 0x1

_PDF_HEADER = re.compile(rb"^%PDF-[12]\.\d")
_ZIP_HEADER = b"PK\x03\x04"
_ALLOWED_CONTROLS = frozenset("\t\n\r\f")
_READ_SIZE = 64 * 1024


class UnsupportedFileTypeError(UnsupportedMediaTypeError):
    default_code = "UNSUPPORTED_FILE_TYPE"
    default_message = (
        "Unsupported file type. Upload a PDF, a Word document (.docx), or UTF-8 text or Markdown."
    )


class EmptyFileError(ValidationFailedError):
    default_code = "FILE_EMPTY"
    default_message = "The file is empty."


@dataclass(frozen=True, slots=True)
class DetectedType:
    mime_type: str
    kind: str  # "pdf" | "docx" | "text" (selects the parser in Phase 6)


def detect_file_type(file: IO[bytes], filename: str) -> DetectedType:
    """Inspect ``file`` (seekable, any position) and return its real type, or raise.

    The file position is restored to the start afterwards.
    """
    try:
        file.seek(0)
        head = file.read(16)
        if not head:
            raise EmptyFileError
        if _PDF_HEADER.match(head):
            return DetectedType(PDF_MIME, "pdf")
        if head.startswith(_ZIP_HEADER):
            _check_docx(file)
            return DetectedType(DOCX_MIME, "docx")
        _check_utf8_text(file)
        if extension_of(filename) in MARKDOWN_EXTENSIONS:
            return DetectedType(MARKDOWN_MIME, "text")
        return DetectedType(TEXT_MIME, "text")
    finally:
        file.seek(0)


def _unsupported(reason: str) -> UnsupportedFileTypeError:
    return UnsupportedFileTypeError(details={"reason": reason})


def _check_docx(file: IO[bytes]) -> None:
    file.seek(0)
    try:
        with zipfile.ZipFile(file) as archive:
            entries = archive.infolist()
            if len(entries) > DOCX_MAX_ENTRIES:
                raise _unsupported("archive_too_many_entries")
            if sum(entry.file_size for entry in entries) > DOCX_MAX_UNCOMPRESSED_BYTES:
                raise _unsupported("archive_too_large_uncompressed")
            if any(entry.flag_bits & _ZIP_ENCRYPTED_FLAG for entry in entries):
                raise _unsupported("encrypted")
            names = {entry.filename for entry in entries}
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise _unsupported("macros")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise _unsupported("not_a_word_document")
            info = archive.getinfo("[Content_Types].xml")
            if info.file_size > DOCX_MAX_CONTENT_TYPES_BYTES:
                raise _unsupported("archive_too_large_uncompressed")
            with archive.open(info) as part:
                # Read at most limit + 1 bytes: declared sizes can lie.
                content_types = part.read(DOCX_MAX_CONTENT_TYPES_BYTES + 1)
            if len(content_types) > DOCX_MAX_CONTENT_TYPES_BYTES:
                raise _unsupported("archive_too_large_uncompressed")
            if DOCX_MAIN_CONTENT_TYPE not in content_types:
                # .docm (macro-enabled), .dotx/.dotm (templates), .xlsx, .pptx, plain ZIPs.
                raise _unsupported("not_a_word_document")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError) as exc:
        # NotImplementedError: unsupported compression method; EOFError: truncated archive.
        raise _unsupported("corrupt_archive") from exc


def _check_utf8_text(file: IO[bytes]) -> None:
    file.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        while chunk := file.read(_READ_SIZE):
            _check_characters(decoder.decode(chunk))
        _check_characters(decoder.decode(b"", final=True))
    except UnicodeDecodeError as exc:
        raise _unsupported("not_utf8_text") from exc


def _check_characters(text: str) -> None:
    for ch in text:
        if (ch < " " and ch not in _ALLOWED_CONTROLS) or ch == "\x7f":
            raise _unsupported("binary_content")

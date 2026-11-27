"""File type detection from content, and filename sanitization (TASK-04.03)."""

import io
import zipfile

import pytest

from app.uploads import detect as detect_module
from app.uploads.detect import (
    DOCX_MAIN_CONTENT_TYPE,
    DOCX_MIME,
    MARKDOWN_MIME,
    PDF_MIME,
    TEXT_MIME,
    EmptyFileError,
    UnsupportedFileTypeError,
    detect_file_type,
)
from app.uploads.filenames import MAX_FILENAME_LENGTH, extension_of, sanitize_filename

# --- crafted files --------------------------------------------------------------------------

MINIMAL_PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"


def content_types(main: bytes = DOCX_MAIN_CONTENT_TYPE) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
        b'package/2006/content-types"><Override PartName="/word/document.xml" ContentType="'
        + main
        + b'"/></Types>'
    )


def make_zip(entries: dict[str, bytes], *, encrypted: set[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    data = bytearray(buffer.getvalue())
    # zipfile resets flag bits when writing, so mark entries "encrypted" in the central
    # directory afterwards (signature PK; flags at +8; name length at +28; name at +46).
    position = data.find(b"PK")
    while position != -1:
        name_length = int.from_bytes(data[position + 28 : position + 30], "little")
        name = bytes(data[position + 46 : position + 46 + name_length]).decode()
        if encrypted and name in encrypted:
            data[position + 8] |= 0x1
        position = data.find(b"PK", position + 46)
    return bytes(data)


def make_docx(**extra: bytes) -> bytes:
    return make_zip(
        {
            "[Content_Types].xml": content_types(),
            "word/document.xml": b"<w:document><w:body><w:p>Hello</w:p></w:body></w:document>",
            **extra,
        }
    )


def detect(data: bytes, filename: str = "upload") -> str:
    return detect_file_type(io.BytesIO(data), filename).mime_type


def rejection(data: bytes, filename: str = "upload") -> str:
    with pytest.raises(UnsupportedFileTypeError) as caught:
        detect_file_type(io.BytesIO(data), filename)
    assert caught.value.code == "UNSUPPORTED_FILE_TYPE"
    details = caught.value.details or {}
    return str(details["reason"])


# --- accepted -------------------------------------------------------------------------------


@pytest.mark.parametrize("header", [b"%PDF-1.4", b"%PDF-1.7", b"%PDF-2.0"])
def test_pdf_by_header(header: bytes) -> None:
    assert detect(header + MINIMAL_PDF[8:], "anything.bin") == PDF_MIME


def test_docx_by_structure_whatever_the_name() -> None:
    assert detect(make_docx(), "report.pdf") == DOCX_MIME


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.md", MARKDOWN_MIME),
        ("NOTES.MD", MARKDOWN_MIME),
        ("notes.markdown", MARKDOWN_MIME),
        ("notes.txt", TEXT_MIME),
        ("notes", TEXT_MIME),
        ("notes.md.exe", TEXT_MIME),  # only the real last extension counts
    ],
)
def test_text_and_markdown(filename: str, expected: str) -> None:
    assert detect(b"# Title\n\nSome text\twith a tab.\r\n", filename) == expected


def test_utf8_with_bom_and_non_ascii_text() -> None:
    assert detect("\ufeffCafé, naïve, 日本語, emoji 🎉\n".encode()) == TEXT_MIME


def test_multibyte_character_split_across_read_boundary() -> None:
    data = b"a" * (64 * 1024 - 1) + "é".encode() + b" end"  # é straddles the 64 KiB read
    assert detect(data) == TEXT_MIME


def test_polyglot_with_a_pdf_header_is_treated_as_pdf() -> None:
    # Documented choice: the header decides. It is stored as application/pdf and always
    # downloaded as an attachment, so embedded HTML can never render.
    assert detect(b"%PDF-1.7\n<html><script>alert(1)</script></html>") == PDF_MIME


def test_detection_rewinds_the_file() -> None:
    file = io.BytesIO(make_docx())
    file.seek(10)
    detect_file_type(file, "a.docx")
    assert file.tell() == 0


# --- refused --------------------------------------------------------------------------------


def test_empty_file_is_422() -> None:
    with pytest.raises(EmptyFileError) as caught:
        detect_file_type(io.BytesIO(b""), "empty.txt")
    assert caught.value.code == "FILE_EMPTY"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", "not_utf8_text"),  # Windows .exe named .pdf
        (b"\x7fELF\x02\x01\x01\x00", "binary_content"),  # Linux executable
        (b"\x89PNG\r\n\x1a\n\x00\x00", "not_utf8_text"),
        (b"plain text with a NUL\x00byte", "binary_content"),
        (b"bell \x07 character", "binary_content"),
        ("café".encode("latin-1"), "not_utf8_text"),  # text, but not UTF-8
        (b"%PDF-x not a version", "binary_content"),  # not a PDF header... and see below
    ],
)
def test_non_documents_are_refused(data: bytes, reason: str) -> None:
    if data.startswith(b"%PDF-x"):
        # A broken PDF header is plain UTF-8 text, so it is accepted as text, not as PDF.
        assert detect(data) == TEXT_MIME
        return
    assert rejection(data, "invoice.pdf") == reason


@pytest.mark.parametrize(
    ("main", "what"),
    [
        (b"application/vnd.ms-word.document.macroEnabled.main+xml", "docm"),
        (
            b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
            "dotx",
        ),
        (b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", "xlsx"),
    ],
)
def test_other_office_zips_are_refused(main: bytes, what: str) -> None:
    archive = make_zip(
        {"[Content_Types].xml": content_types(main), "word/document.xml": b"<w:document/>"}
    )
    assert rejection(archive, f"file.{what}") == "not_a_word_document"


@pytest.mark.parametrize(
    "entries",
    [
        {"[Content_Types].xml": content_types()},  # no word/document.xml
        {"word/document.xml": b"<w:document/>"},  # no [Content_Types].xml
        {"readme.txt": b"just a zip"},
    ],
)
def test_zips_that_are_not_word_documents(entries: dict[str, bytes]) -> None:
    assert rejection(make_zip(entries)) == "not_a_word_document"


def test_macros_are_refused_even_with_a_docx_content_type() -> None:
    assert rejection(make_docx(**{"word/vbaProject.bin": b"\x00macro"})) == "macros"


def test_encrypted_entries_are_refused() -> None:
    archive = make_zip(
        {"[Content_Types].xml": content_types(), "word/document.xml": b"<w:document/>"},
        encrypted={"word/document.xml"},
    )
    assert rejection(archive) == "encrypted"


@pytest.mark.parametrize("data", [b"PK\x03\x04" + b"\x00" * 50, make_docx()[:200]])
def test_corrupt_or_truncated_archives(data: bytes) -> None:
    assert rejection(data) == "corrupt_archive"


def test_zip_bomb_by_declared_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect_module, "DOCX_MAX_UNCOMPRESSED_BYTES", 10_000)
    bomb = make_docx(**{"word/media/zeros.bin": b"\x00" * 20_000})  # compresses to ~40 bytes
    assert len(bomb) < 2_000
    assert rejection(bomb) == "archive_too_large_uncompressed"


def test_zip_with_too_many_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect_module, "DOCX_MAX_ENTRIES", 3)
    archive = make_docx(**{f"word/part{i}.xml": b"<x/>" for i in range(2)})
    assert rejection(archive) == "archive_too_many_entries"


def test_oversized_content_types_part(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect_module, "DOCX_MAX_CONTENT_TYPES_BYTES", 50)
    assert rejection(make_docx()) == "archive_too_large_uncompressed"


# --- filenames ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("C:\\Users\\alice\\Desktop\\report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("  spaced   out   name.txt  ", "spaced out name.txt"),
        ("invoice\u202efdp.exe", "invoicefdp.exe"),  # right-to-left override removed
        ("zero\u200bwidth.txt", "zerowidth.txt"),
        ("tab\tand\nnewline.md", "tab and newline.md"),  # \t, \n are whitespace → spaces
        ("bell\x07.txt", "bell.txt"),
        (".htaccess", "htaccess"),
        ("trailing dots...", "trailing dots"),
        ("e\u0301te\u0301.docx", "été.docx"),  # NFC
        ("", "upload"),
        (None, "upload"),
        ("...", "upload"),
        ("dir/", "upload"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_long_names_keep_their_extension() -> None:
    name = sanitize_filename("a" * 400 + ".docx")
    assert len(name) == MAX_FILENAME_LENGTH
    assert name.endswith("a.docx")


def test_long_name_with_an_absurd_extension_is_cut_plainly() -> None:
    name = sanitize_filename("a." + "b" * 400)
    assert len(name) == MAX_FILENAME_LENGTH


@pytest.mark.parametrize(
    ("name", "ext"), [("a.PDF", "pdf"), ("archive.tar.gz", "gz"), ("noext", ""), (".md", "")]
)
def test_extension_of(name: str, ext: str) -> None:
    assert extension_of(name) == ext

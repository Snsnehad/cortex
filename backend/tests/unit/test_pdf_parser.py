"""PDF text extraction (TASK-06.01). Fixture PDFs are generated here with pypdf, so no binary
files live in the repository and every case shows exactly what it contains."""

import io
from collections.abc import Sequence

import pytest
from pypdf import PageObject, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.rag.ingestion.parsed import ExtractionError, ParsedDocument
from app.rag.ingestion.pdf import EXTRACTION_EMPTY, PDF_ENCRYPTED, PDF_INVALID, PdfParser

Line = tuple[int, int, str]  # x, y, text


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(
    pages: Sequence[Sequence[Line]],
    *,
    image_pages: Sequence[int] = (),
    encrypt: dict[str, str] | None = None,
) -> bytes:
    """A PDF whose page i draws ``pages[i]`` as text lines (Helvetica 12), and an image on
    the pages listed in ``image_pages`` (0-based)."""
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                # As real PDFs do: without an encoding, bytes above 127 have no meaning.
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    image = DecodedStreamObject()
    image.set_data(b"\x80" * 4)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(2),
            NameObject("/Height"): NumberObject(2),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_ref = writer._add_object(image)
    for index, lines in enumerate(pages):
        page = writer.add_blank_page(612, 792)
        ops = []
        if lines:
            ops.append("BT /F1 12 Tf")
            ops += [f"1 0 0 1 {x} {y} Tm ({_escape(text)}) Tj" for x, y, text in lines]
            ops.append("ET")
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        if index in image_pages:
            ops.append("q 400 0 0 500 100 150 cm /Im1 Do Q")
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): image_ref})
        content = DecodedStreamObject()
        content.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = resources
        page[NameObject("/ProcSet")] = ArrayObject([NameObject("/PDF"), NameObject("/Text")])
    if encrypt is not None:
        writer.encrypt(**encrypt)  # type: ignore[arg-type]
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def text_page(*lines: str) -> list[Line]:
    return [(72, 720 - 20 * i, line) for i, line in enumerate(lines)]


def parse(data: bytes) -> ParsedDocument:
    return PdfParser().parse(io.BytesIO(data))


def failure(data: bytes) -> ExtractionError:
    with pytest.raises(ExtractionError) as caught:
        parse(data)
    return caught.value


# --- text -----------------------------------------------------------------------------------


def test_one_block_per_page_with_its_page_number() -> None:
    document = parse(
        make_pdf([text_page("Vacation policy", "25 days per year"), text_page("Sick leave")])
    )

    assert document.page_count == 2
    assert [b.page for b in document.blocks] == [1, 2]
    assert "Vacation policy" in document.blocks[0].text
    assert "25 days per year" in document.blocks[0].text
    assert "Sick leave" in document.blocks[1].text
    assert document.stats == {"pages": 2, "empty_pages": 0, "failed_pages": 0}


def test_blank_pages_are_skipped_but_numbering_is_kept() -> None:
    document = parse(make_pdf([text_page("Cover"), [], text_page("Chapter one")]))

    assert [b.page for b in document.blocks] == [1, 3]
    assert document.stats["empty_pages"] == 1


def test_two_columns_come_out_column_by_column() -> None:
    left = [(72, 720 - 20 * i, f"Left column line {i}") for i in range(3)]
    right = [(330, 720 - 20 * i, f"Right column line {i}") for i in range(3)]

    text = parse(make_pdf([left + right])).blocks[0].text

    assert text.index("Left column line 2") < text.index("Right column line 0")
    assert "Left column line 0 Right column line 0" not in text  # not interleaved by line


def test_special_characters_survive() -> None:
    document = parse(make_pdf([text_page("Costs (net): 100% - café")]))
    assert "Costs (net): 100% - café" in document.blocks[0].text


def test_the_file_is_read_from_the_start() -> None:
    file = io.BytesIO(make_pdf([text_page("Start")]))
    file.seek(50)
    assert "Start" in PdfParser().parse(file).blocks[0].text


# --- encryption -----------------------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_a_real_password_is_refused(algorithm: str) -> None:
    data = make_pdf(
        [text_page("Secret")],
        encrypt={"user_password": "s3cret", "owner_password": "owner", "algorithm": algorithm},
    )

    error = failure(data)

    assert error.code == PDF_ENCRYPTED
    assert "password-protected" in error.message


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_an_empty_user_password_only_restricts_and_opens(algorithm: str) -> None:
    """Common in the wild: "encrypted" only to forbid printing/copying, no password to open."""
    data = make_pdf(
        [text_page("Printable? No. Readable? Yes.")],
        encrypt={"user_password": "", "owner_password": "owner", "algorithm": algorithm},
    )

    assert "Readable? Yes." in parse(data).blocks[0].text


# --- nothing to extract ---------------------------------------------------------------------


def test_an_image_only_pdf_is_reported_as_a_scan() -> None:
    error = failure(make_pdf([[], []], image_pages=[0, 1]))

    assert error.code == EXTRACTION_EMPTY
    assert "OCR" in error.message


def test_a_pdf_without_text_or_images_is_empty() -> None:
    error = failure(make_pdf([[], []]))

    assert error.code == EXTRACTION_EMPTY
    assert error.message == "This PDF contains no text."


def test_a_partly_scanned_pdf_keeps_its_text_pages() -> None:
    document = parse(make_pdf([text_page("Typed page"), []], image_pages=[1]))

    assert [b.page for b in document.blocks] == [1]
    assert document.stats["empty_pages"] == 1


# --- broken files ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.7\n" + b"\x00garbage" * 50,
        b"not a pdf at all",
        b"",
    ],
)
def test_unreadable_files_are_invalid(data: bytes) -> None:
    assert failure(data).code == PDF_INVALID


def test_a_page_that_fails_is_skipped_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_pdf([text_page("Page one"), text_page("Page two"), text_page("Page three")])
    original = PageObject.extract_text
    calls = {"n": 0}

    def flaky(self: PageObject, *args: object, **kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("broken content stream")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PageObject, "extract_text", flaky)

    document = parse(data)

    assert [b.page for b in document.blocks] == [1, 3]
    assert document.stats["failed_pages"] == 1


def test_when_every_page_fails_the_pdf_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: PageObject, *args: object, **kwargs: object) -> str:
        raise ValueError("broken content stream")

    monkeypatch.setattr(PageObject, "extract_text", broken)

    assert failure(make_pdf([text_page("a"), text_page("b")])).code == PDF_INVALID


def test_messages_never_contain_document_text() -> None:
    data = make_pdf(
        [text_page("CONFIDENTIAL salary table")],
        encrypt={"user_password": "pw", "owner_password": "o", "algorithm": "AES-256"},
    )
    assert "CONFIDENTIAL" not in failure(data).message

"""Word (.docx) extraction (TASK-06.02). Documents are assembled here from raw
WordprocessingML, so each test shows exactly the XML it feeds the parser."""

import io
import tracemalloc
import zipfile

import pytest

from app.rag.ingestion import docx as docx_module
from app.rag.ingestion.archive import ARCHIVE_TOO_LARGE
from app.rag.ingestion.docx import DOCX_INVALID, EXTRACTION_EMPTY, DocxParser
from app.rag.ingestion.parsed import ExtractionError, ParsedDocument

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
    'package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
STYLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{W_NS}">
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/></w:style>
  <w:style w:type="paragraph" w:styleId="Chapter"><w:name w:val="Chapter"/>
    <w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/></w:style>
  <w:style w:type="character" w:styleId="Heading3Char"><w:name w:val="heading 3"/></w:style>
</w:styles>"""


def p(
    text: str = "", style: str | None = None, *, outline: int | None = None, runs: str = ""
) -> str:
    props = ""
    if style or outline is not None:
        props = "<w:pPr>"
        props += f'<w:pStyle w:val="{style}"/>' if style else ""
        props += f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else ""
        props += "</w:pPr>"
    body = f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>' if text else ""
    return f"<w:p>{props}{body}{runs}</w:p>"


def table(*rows: list[str]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{p(cell)}</w:tc>" for cell in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"


def make_docx(body: str, *, styles: str | None = STYLES, document: str | None = None) -> bytes:
    xml = document or (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W_NS}"><w:body>'
        f"{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", xml)
        if styles is not None:
            archive.writestr("word/styles.xml", styles)
    return buffer.getvalue()


def parse(data: bytes) -> ParsedDocument:
    return DocxParser().parse(io.BytesIO(data))


def failure(data: bytes) -> ExtractionError:
    with pytest.raises(ExtractionError) as caught:
        parse(data)
    return caught.value


def paths_and_text(document: ParsedDocument) -> list[tuple[tuple[str, ...], str]]:
    return [(block.heading_path, block.text) for block in document.blocks]


# --- structure ------------------------------------------------------------------------------


def test_headings_become_section_paths() -> None:
    body = (
        p("Intro text before any heading.")
        + p("Benefits", "Heading1")
        + p("Overview of benefits.")
        + p("Vacation", "Heading2")
        + p("25 days per year.")
        + p("Sick leave", "Heading2")
        + p("Paid from day one.")
        + p("Conduct", "Heading1")
        + p("Be kind.")
    )

    assert paths_and_text(parse(make_docx(body))) == [
        ((), "Intro text before any heading."),
        (("Benefits",), "Benefits\n\nOverview of benefits."),
        (("Benefits", "Vacation"), "Vacation\n\n25 days per year."),
        (("Benefits", "Sick leave"), "Sick leave\n\nPaid from day one."),
        (("Conduct",), "Conduct\n\nBe kind."),
    ]


def test_outline_levels_make_headings_too() -> None:
    body = (
        p("Part one", "Chapter")  # style with an outline level
        + p("Section A", outline=1)  # paragraph-level outline level
        + p("Text.")
        + p("Not a heading", outline=9)  # 9 = body text
    )

    assert paths_and_text(parse(make_docx(body))) == [
        (("Part one",), "Part one"),  # a heading with no text of its own still counts
        (("Part one", "Section A"), "Section A\n\nText.\n\nNot a heading"),
    ]


def test_character_styles_and_unknown_styles_are_not_headings() -> None:
    body = p("A quote", "Quote") + p("Odd", "Heading3Char") + p("Plain", "NoSuchStyle")
    assert paths_and_text(parse(make_docx(body))) == [((), "A quote\n\nOdd\n\nPlain")]


def test_works_without_a_styles_part() -> None:
    body = p("Title", "Heading1") + p("Body")
    assert paths_and_text(parse(make_docx(body, styles=None))) == [((), "Title\n\nBody")]


def test_a_document_of_only_headings_keeps_them() -> None:
    assert paths_and_text(parse(make_docx(p("Only a title", "Heading1")))) == [
        (("Only a title",), "Only a title")
    ]


# --- text details ---------------------------------------------------------------------------


def test_tabs_breaks_and_hyphens() -> None:
    runs = (
        "<w:r><w:t>Name</w:t><w:tab/><w:t>Value</w:t><w:br/><w:t>next</w:t>"
        "<w:noBreakHyphen/><w:t>line</w:t></w:r>"
    )
    assert parse(make_docx(p(runs=runs))).blocks[0].text == "Name\tValue\nnext-line"


def test_tracked_deletions_and_field_codes_are_not_text() -> None:
    runs = (
        "<w:del><w:r><w:delText>removed words </w:delText></w:r></w:del>"
        "<w:ins><w:r><w:t>inserted words</w:t></w:r></w:ins>"
        "<w:r><w:instrText> PAGE \\* MERGEFORMAT </w:instrText></w:r>"
    )
    text = parse(make_docx(p(runs=runs))).blocks[0].text
    assert text == "inserted words"


def test_content_controls_are_read() -> None:
    body = (
        "<w:sdt><w:sdtContent>"
        + p("Inside a content control")
        + table(["a", "b"])
        + "</w:sdtContent></w:sdt>"
    )
    assert parse(make_docx(body)).blocks[0].text == "Inside a content control\n\na | b"


def test_a_text_box_inside_a_paragraph_is_read_once() -> None:
    text_box = (
        "<w:r><w:drawing><w:txbxContent>"
        + p("Text in a box")
        + "</w:txbxContent></w:drawing></w:r>"
    )
    body = p("Before the box.", runs=text_box)
    text = parse(make_docx(body)).blocks[0].text
    assert text.count("Text in a box") == 1


# --- tables ---------------------------------------------------------------------------------


def test_tables_become_one_line_per_row() -> None:
    body = p("Prices:") + table(["Plan", "Price"], ["Basic", "10 EUR"], ["", ""], ["Pro", "25 EUR"])
    document = parse(make_docx(body))

    assert document.blocks[0].text == "Prices:\n\nPlan | Price\nBasic | 10 EUR\nPro | 25 EUR"
    assert document.stats["tables"] == 1


def test_a_nested_table_is_flattened_into_its_cell() -> None:
    inner = table(["x", "y"])
    body = f"<w:tbl><w:tr><w:tc>{p('outer')}</w:tc><w:tc>{inner}</w:tc></w:tr></w:tbl>"
    assert parse(make_docx(body)).blocks[0].text == "outer | x y"


# --- refusals -------------------------------------------------------------------------------


def test_an_empty_document_is_empty() -> None:
    error = failure(make_docx(p() + p("   ")))
    assert (error.code, error.message) == (EXTRACTION_EMPTY, "This Word document contains no text.")


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip at all",
        b"PK\x03\x04" + b"\x00" * 40,
        make_docx("", document="<w:document><unclosed>"),
    ],
)
def test_unreadable_documents_are_invalid(data: bytes) -> None:
    assert failure(data).code == DOCX_INVALID


def test_a_zip_without_a_word_document_is_invalid() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    assert failure(buffer.getvalue()).code == DOCX_INVALID


def test_xml_entity_tricks_are_refused() -> None:
    billion_laughs = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    assert failure(make_docx("", document=billion_laughs)).code == DOCX_INVALID


def test_an_encrypted_entry_is_invalid() -> None:
    data = bytearray(make_docx(p("secret")))
    position = data.find(b"PK\x01\x02")
    while position != -1:  # mark every central-directory entry as encrypted
        data[position + 8] |= 0x1
        position = data.find(b"PK\x01\x02", position + 4)
    assert failure(bytes(data)).code == DOCX_INVALID


def test_a_zip_bomb_is_stopped_at_the_cap_with_little_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """20 MB of XML compressed into a few kilobytes; the cap is lowered to 1 MB so the test
    stays fast. The parser must stop at the cap and never hold the whole entry in memory."""
    monkeypatch.setattr(docx_module, "DOCUMENT_XML_LIMIT", 1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        with archive.open("word/document.xml", "w") as entry:  # streamed: no 20 MB string
            entry.write(f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>'.encode())
            for _ in range(20 * 16):
                entry.write(b"a" * 65536)
            entry.write(b"</w:t></w:r></w:p></w:body></w:document>")
    data = buffer.getvalue()
    assert len(data) < 200_000  # it really is a bomb

    tracemalloc.start()
    try:
        error = failure(data)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert error.code == ARCHIVE_TOO_LARGE
    assert peak < 8 * 1024 * 1024  # far below the 20 MB it would inflate to


def test_stats_and_reading_from_the_start() -> None:
    file = io.BytesIO(make_docx(p("H", "Heading1") + p("x") + table(["a"])))
    file.seek(10)

    document = DocxParser().parse(file)

    assert document.stats["headings"] == 1
    assert document.stats["tables"] == 1
    assert document.stats["xml_bytes"] > 0
    assert document.page_count is None  # Word documents have no fixed pages


def test_memory_stays_flat_for_a_long_document() -> None:
    """50,000 paragraphs: measured 5.8 MiB peak when each finished element is discarded,
    22.6 MiB when the parsed tree is kept (it grows with the document)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        with archive.open("word/document.xml", "w") as entry:
            entry.write(f'<w:document xmlns:w="{W_NS}"><w:body>'.encode())
            for i in range(50_000):
                entry.write(
                    f"<w:p><w:r><w:t>Paragraph {i} of the handbook.</w:t></w:r></w:p>".encode()
                )
            entry.write(b"</w:body></w:document>")
    data = buffer.getvalue()

    tracemalloc.start()
    try:
        document = parse(data)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert document.text_length > 1_500_000
    assert peak < 12 * 1024 * 1024

"""Word (.docx) text extraction, streaming and memory-bounded (TASK-06.02).

Built on the standard library (``zipfile`` + ``xml.etree`` pull parsing) rather than
python-docx, on purpose:

* python-docx loads the whole package into memory, images included; this reads only
  ``word/document.xml`` and ``word/styles.xml``, element by element, discarding each
  top-level element once handled, so memory stays flat even for very long documents;
* every entry is read through ``LimitedReader``, which caps the bytes *actually*
  decompressed: a zip bomb is stopped at the cap, whatever sizes the archive declares;
* an XML ``DOCTYPE`` is refused: Word never writes one, and it is the only way to declare
  entities (billion-laughs expansion, external entities).

What comes out:

* **Headings** (styles named "Heading N", or any style/paragraph with an outline level)
  open a section: following text gets ``heading_path`` like ("Benefits", "Vacation"), and
  the heading line itself starts the section's text (good for keyword search).
* **Paragraphs** in document order; tabs and line breaks kept.
* **Tables**: one line per row, cells joined with " | "; nested tables are flattened into
  their cell.
* Skipped: deleted text of tracked changes (``w:delText``), field codes
  (``w:instrText``), headers, footers, comments and images.

Errors: ``DOCX_INVALID`` (not a readable Word document), ``ARCHIVE_TOO_LARGE`` (the cap),
``EXTRACTION_EMPTY`` (no text).
"""

import re
import zipfile
from collections.abc import Iterator
from typing import IO, Literal
from xml.etree.ElementTree import Element, ParseError, XMLPullParser

from app.rag.ingestion.archive import LimitedReader, open_entry
from app.rag.ingestion.parsed import ExtractionError, ParsedDocument, TextBlock

DOCX_INVALID = "DOCX_INVALID"
EXTRACTION_EMPTY = "EXTRACTION_EMPTY"

DOCUMENT_XML_LIMIT = 64 * 1024 * 1024  # decompressed bytes of word/document.xml
STYLES_XML_LIMIT = 8 * 1024 * 1024
_CHUNK = 64 * 1024
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_NAME = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)
_BODY_TEXT_OUTLINE_LEVEL = 9  # w:outlineLvl 9 means "body text", not a heading


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element: Element | None, name: str) -> str | None:
    return None if element is None else element.get(_W + name)


def _elements(
    reader: LimitedReader, events: tuple[Literal["start", "end"], ...]
) -> Iterator[tuple[str, Element]]:
    """Stream (event, element) pairs from an XML entry; refuse a DOCTYPE."""
    parser: XMLPullParser[Element] = XMLPullParser(events=events)
    head = reader.read(_CHUNK)
    if b"<!doctype" in head.lower():
        raise ExtractionError(DOCX_INVALID, "The Word document contains unsupported XML.")
    chunk = head
    while chunk:
        parser.feed(chunk)
        yield from parser.read_events()  # type: ignore[misc]
        chunk = reader.read(_CHUNK)
    parser.close()
    yield from parser.read_events()  # type: ignore[misc]


def _heading_levels(archive: zipfile.ZipFile) -> dict[str, int]:
    """Paragraph style id → heading level (1-based), from word/styles.xml."""
    if "word/styles.xml" not in archive.namelist():
        return {}
    levels: dict[str, int] = {}
    reader = open_entry(archive, "word/styles.xml", limit=STYLES_XML_LIMIT)
    try:
        for _event, element in _elements(reader, ("end",)):
            if element.tag != _W + "style" or _attr(element, "type") != "paragraph":
                continue
            style_id = _attr(element, "styleId")
            name = _attr(element.find(_W + "name"), "val") or ""
            outline = _attr(element.find(f"{_W}pPr/{_W}outlineLvl"), "val")
            match = _HEADING_NAME.match(name.strip())
            if style_id and match:
                levels[style_id] = int(match.group(1))
            elif (
                style_id
                and outline
                and outline.isdigit()
                and int(outline) < _BODY_TEXT_OUTLINE_LEVEL
            ):
                levels[style_id] = int(outline) + 1
            element.clear()
    finally:
        reader.close()
    return levels


def _text(element: Element) -> str:
    """Visible text of a paragraph (or cell): w:t, with tabs and breaks; deleted text and
    field codes are different tags and therefore skipped."""
    parts: list[str] = []
    for node in element.iter():
        tag = node.tag
        if tag == _W + "t":
            parts.append(node.text or "")
        elif tag == _W + "tab":
            parts.append("\t")
        elif tag in (_W + "br", _W + "cr"):
            parts.append("\n")
        elif tag == _W + "noBreakHyphen":
            parts.append("-")
    return "".join(parts)


def _cell_text(cell: Element) -> str:
    paragraphs = [_text(p) for p in cell.iter(_W + "p")]
    return " ".join(text.strip() for text in paragraphs if text.strip())


def _table_text(table: Element) -> str:
    rows = []
    for row in table.findall(_W + "tr"):
        cells = [_cell_text(cell) for cell in row.findall(_W + "tc")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _heading_level(paragraph: Element, styles: dict[str, int]) -> int | None:
    properties = paragraph.find(_W + "pPr")
    if properties is None:
        return None
    outline = _attr(properties.find(_W + "outlineLvl"), "val")
    if outline and outline.isdigit() and int(outline) < _BODY_TEXT_OUTLINE_LEVEL:
        return int(outline) + 1
    style = _attr(properties.find(_W + "pStyle"), "val")
    return styles.get(style) if style else None


class _Sections:
    """Collects text into blocks, one per heading path."""

    def __init__(self) -> None:
        self.blocks: list[TextBlock] = []
        self._path: list[tuple[int, str]] = []
        self._lines: list[str] = []

    def heading(self, level: int, text: str) -> None:
        self._flush()
        self._path = [(lvl, t) for lvl, t in self._path if lvl < level] + [(level, text)]
        self._lines = [text]

    def add(self, text: str) -> None:
        if text.strip():
            self._lines.append(text.strip("\n"))

    def finish(self) -> list[TextBlock]:
        self._flush()
        return self.blocks

    def _flush(self) -> None:
        if self._lines:
            path = tuple(text for _level, text in self._path)
            self.blocks.append(TextBlock(text="\n\n".join(self._lines), heading_path=path))
        self._lines = []


class DocxParser:
    def parse(self, file: IO[bytes]) -> ParsedDocument:
        file.seek(0)
        try:
            with zipfile.ZipFile(file) as archive:
                styles = _heading_levels(archive)
                return self._parse_body(archive, styles)
        except ExtractionError:
            raise
        except (zipfile.BadZipFile, KeyError, ParseError, EOFError, NotImplementedError) as exc:
            raise ExtractionError(
                DOCX_INVALID, "The Word document is damaged or not a valid .docx file."
            ) from exc

    @staticmethod
    def _parse_body(archive: zipfile.ZipFile, styles: dict[str, int]) -> ParsedDocument:
        sections = _Sections()
        stack: list[str] = []  # local names of the open elements
        body: Element | None = None
        headings = tables = 0
        reader = open_entry(archive, "word/document.xml", limit=DOCUMENT_XML_LIMIT)
        try:
            for event, element in _elements(reader, ("start", "end")):
                name = _local(element.tag)
                if event == "start":
                    stack.append(name)
                    if name == "body":
                        body = element
                    continue
                stack.pop()
                outermost_paragraph = name == "p" and "p" not in stack and "tbl" not in stack
                outermost_table = name == "tbl" and "tbl" not in stack and "p" not in stack
                if outermost_paragraph:
                    text = _text(element)
                    level = _heading_level(element, styles)
                    if level is not None and text.strip():
                        sections.heading(level, text.strip())
                        headings += 1
                    else:
                        sections.add(text)
                elif outermost_table:
                    sections.add(_table_text(element))
                    tables += 1
                else:
                    continue
                if body is not None and stack and stack[-1] == "body":
                    body.clear()  # done with this top-level element: keep memory flat
        finally:
            reader.close()
        blocks = sections.finish()
        if not blocks:
            raise ExtractionError(EXTRACTION_EMPTY, "This Word document contains no text.")
        return ParsedDocument(
            blocks=blocks,
            stats={"headings": headings, "tables": tables, "xml_bytes": reader.bytes_read},
        )

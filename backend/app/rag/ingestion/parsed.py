"""What a parser produces, whatever the file type (TASK-06.01).

One shape serves both "pages" (PDF) and "sections" (DOCX, Markdown): an ordered list of text
blocks. A block carries its page number when the format has pages, and the heading path it
sits under when the format has headings. The chunker (TASK-06.05) needs exactly these two
things to give every chunk its ``page_start``/``page_end`` and ``section_path``.

Parsers are synchronous and CPU-bound: call them from a worker thread. They read a
seekable binary file and never hold more than the extracted text in memory.

Failures that retrying cannot fix raise ``ExtractionError(code, message)``; the pipeline
(TASK-06.06) records them as permanent job failures. Messages are shown to users and never
contain document text.
"""

from dataclasses import dataclass, field
from typing import IO, Protocol


class ExtractionError(Exception):
    """The file cannot be turned into text (permanent: retrying will not help)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    page: int | None = None  # 1-based page number, for paged formats
    heading_path: tuple[str, ...] = ()  # e.g. ("Benefits", "Vacation"), for structured formats


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: list[TextBlock]
    page_count: int | None = None  # None for formats without pages
    # Counts for the job's metrics (never document text).
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def text_length(self) -> int:
        return sum(len(block.text) for block in self.blocks)


class Parser(Protocol):
    def parse(self, file: IO[bytes]) -> ParsedDocument:
        """Extract text from ``file`` (seekable; read from the start). Raises
        ``ExtractionError`` for unusable files."""
        ...

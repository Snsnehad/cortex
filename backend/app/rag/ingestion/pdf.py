"""PDF text extraction with pypdf (ADR-009), one block per page (TASK-06.01).

Outcomes:

* **Encrypted** with a real password → ``PDF_ENCRYPTED``. Many PDFs are "encrypted" with an
  *empty* user password only to restrict printing or copying; those open normally (AES needs
  the ``cryptography`` package, installed through ``pypdf[crypto]``).
* **Not a readable PDF** (truncated, corrupt beyond pypdf's repair) → ``PDF_INVALID``.
* **No text at all** → ``EXTRACTION_EMPTY``. If its pages carry images, the PDF is almost
  certainly scanned: the message says that OCR is not supported (``details`` in stats).
* A page whose text cannot be extracted is skipped and counted (``failed_pages``), as long
  as at least one page worked; one bad page should not lose a 300-page manual.

Text order follows the PDF's content stream (pypdf "plain" mode). For a typical
multi-column layout, where each column is written as a run, that yields column by column,
not lines interleaved across columns.

Limits on pages and characters are TASK-06.07; time is bounded by the task's time limit.
"""

from typing import IO, Any

from pypdf import PageObject, PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError, PyPdfError

from app.core.logging import get_logger
from app.rag.ingestion.parsed import ExtractionError, ParsedDocument, TextBlock

logger = get_logger("cortex.ingestion")

PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_INVALID = "PDF_INVALID"
EXTRACTION_EMPTY = "EXTRACTION_EMPTY"


def _has_images(page: PageObject) -> bool:
    """Whether the page draws image XObjects (without decoding them: no memory spent)."""
    try:
        resources: Any = page.get("/Resources")
        xobjects: Any = resources.get_object().get("/XObject") if resources else None
        if not xobjects:
            return False
        return any(
            xobject.get_object().get("/Subtype") == "/Image"
            for xobject in xobjects.get_object().values()
        )
    except (PyPdfError, AttributeError, KeyError, TypeError, ValueError):
        return False


class PdfParser:
    def parse(self, file: IO[bytes]) -> ParsedDocument:
        # No seek needed: pypdf positions the stream itself (it reads the trailer at the end).
        try:
            reader = PdfReader(file, strict=False)
            if reader.is_encrypted:
                self._open_encrypted(reader)
            pages = list(reader.pages)
        except ExtractionError:
            raise
        except (PdfReadError, PyPdfError, ValueError, KeyError, TypeError) as exc:
            raise ExtractionError(
                PDF_INVALID, "The PDF file is damaged or not a valid PDF."
            ) from exc

        blocks: list[TextBlock] = []
        failed = empty = with_images = 0
        for number, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except (PyPdfError, ValueError, KeyError, TypeError, AttributeError):
                failed += 1
                logger.warning("pdf.page_failed", page=number)  # page number only, no text
                continue
            if text.strip():
                blocks.append(TextBlock(text=text, page=number))
            else:
                empty += 1
                with_images += _has_images(page)

        stats = {"pages": len(pages), "empty_pages": empty, "failed_pages": failed}
        if pages and failed == len(pages):
            raise ExtractionError(PDF_INVALID, "No page of this PDF could be read.")
        if not blocks:
            if with_images:
                raise ExtractionError(
                    EXTRACTION_EMPTY,
                    "This PDF contains only images (probably a scan). Text recognition (OCR) "
                    "is not supported; upload a PDF with selectable text.",
                )
            raise ExtractionError(EXTRACTION_EMPTY, "This PDF contains no text.")
        return ParsedDocument(blocks=blocks, page_count=len(pages), stats=stats)

    @staticmethod
    def _open_encrypted(reader: PdfReader) -> None:
        """Open a PDF whose user password is empty (restrictions only); refuse real ones."""
        try:
            opened = reader.decrypt("")
        except DependencyError as exc:  # an AES PDF but no crypto library: a deployment bug
            raise RuntimeError("pypdf cannot decrypt AES without 'cryptography'") from exc
        except (FileNotDecryptedError, PdfReadError) as exc:
            raise ExtractionError(PDF_ENCRYPTED, _ENCRYPTED_MESSAGE) from exc
        if not opened:
            raise ExtractionError(PDF_ENCRYPTED, _ENCRYPTED_MESSAGE)


_ENCRYPTED_MESSAGE = "This PDF is password-protected. Remove the password and upload it again."

"""Display names for uploaded files.

The client's filename is **only ever displayed** (lists, citations, the download's
``Content-Disposition``). It never becomes a path or a storage key: those come from ids
(``app.storage.document_key``). Even so it is cleaned, because it is shown to other members:

* only the last path component (``C:\\Users\\x\\a.pdf`` and ``../../a.pdf`` → ``a.pdf``);
* Unicode NFC, so the same name typed on different systems compares equal;
* control and format characters removed, including bidirectional overrides such as U+202E,
  which can make ``invoice‮fdp.exe`` display as ``invoiceexe.pdf``;
* whitespace runs collapsed; leading/trailing spaces and dots removed (``.htaccess`` →
  ``htaccess``; Windows drops trailing dots silently);
* at most ``MAX_FILENAME_LENGTH`` characters, keeping the extension;
* an empty result becomes ``upload``.
"""

import re
import unicodedata

MAX_FILENAME_LENGTH = 255  # documents.filename is varchar(255)
MAX_EXTENSION_LENGTH = 16
FALLBACK_NAME = "upload"
_WHITESPACE = re.compile(r"\s+")


def sanitize_filename(raw: str | None) -> str:
    if not raw:
        return FALLBACK_NAME
    name = re.split(r"[/\\]", raw)[-1]
    name = unicodedata.normalize("NFC", name)
    # Whitespace first (tab, newline … become spaces), so "a<TAB>b" stays two words.
    name = "".join(" " if ch.isspace() else ch for ch in name)
    # Category C*: control (Cc), format (Cf, incl. bidi overrides and zero-width), unassigned
    # and private-use code points.
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if not name:
        return FALLBACK_NAME
    return _truncate(name)


def _truncate(name: str) -> str:
    if len(name) <= MAX_FILENAME_LENGTH:
        return name
    stem, dot, extension = name.rpartition(".")
    if dot and stem and len(extension) <= MAX_EXTENSION_LENGTH:
        keep = MAX_FILENAME_LENGTH - len(extension) - 1
        return f"{stem[:keep].rstrip(' .')}.{extension}"
    return name[:MAX_FILENAME_LENGTH].rstrip(" .")


def extension_of(filename: str) -> str:
    """Lower-case extension without the dot (``"Notes.MD"`` → ``"md"``), or ``""``."""
    stem, dot, extension = filename.rpartition(".")
    return extension.lower() if dot and stem else ""

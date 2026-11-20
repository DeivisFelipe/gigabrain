"""Text of the files the PO attaches in the chat (the minutes of a meeting, business rules...).

Accepted: .txt, .md, .csv, .json (read as they are), .docx (the text of its paragraphs) and
.pdf (the text of its pages, with pypdf). A scanned PDF has no text to read: it comes back
empty and the API says so.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json")
EXTENSIONS = (*TEXT_EXTENSIONS, ".docx", ".pdf")
MAX_FILE_BYTES = 10 * 1024 * 1024  # the same limit the web app shows
MAX_FILES = 5


class DocumentError(ValueError):
    """The file cannot be read (unknown type, too big, damaged)."""


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocumentError("not a valid .docx file") from exc
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = ["".join(t.text or "" for t in p.iter(f"{w}t")) for p in ElementTree.fromstring(xml).iter(f"{w}p")]
    return "\n".join(p for p in paragraphs if p.strip())


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is in requirements.txt
        raise DocumentError("reading PDF needs the pypdf package (pip install pypdf)") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:
        raise DocumentError("not a valid PDF file") from exc


def text_of(name: str, data: bytes) -> str:
    """The text of one file. Raises DocumentError when it cannot be read."""
    extension = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if extension not in EXTENSIONS:
        raise DocumentError(f"{name}: type not accepted (use {', '.join(EXTENSIONS)})")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"{name}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    if extension == ".docx":
        text = _docx(data)
    elif extension == ".pdf":
        text = _pdf(data)
    else:
        text = data.decode("utf-8-sig", errors="replace")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def with_attachments(text: str, files: list[tuple[str, str]]) -> str:
    """The PO's message followed by the text of each attached file, marked with its name."""
    parts = [text.strip()] if text and text.strip() else []
    for name, content in files:
        parts.append(f"--- Attached document: {name} ---\n{content}")
    return "\n\n".join(parts)

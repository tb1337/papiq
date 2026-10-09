"""The PDFium library is not thread-safe, not even for different documents: every use in this
process goes through `LOCK`."""

import threading
from pathlib import Path

import pypdfium2

from papiq.core.domain.errors import UnprocessableDocumentError

LOCK = threading.Lock()


def text_layer(path: Path) -> list[str]:
    """The text of each page of a PDF, as its text layer has it (blocking; run it in a thread)."""
    with LOCK:
        try:
            document = pypdfium2.PdfDocument(path)
        except pypdfium2.PdfiumError as error:
            raise UnprocessableDocumentError(f"the PDF cannot be read: {error}") from None
        try:
            return [page.get_textpage().get_text_range() for page in document]
        finally:
            document.close()


def count_pages(path: Path) -> int:
    """Pages of a PDF (blocking; run it in a thread)."""
    with LOCK:
        try:
            document = pypdfium2.PdfDocument(path)
        except pypdfium2.PdfiumError as error:
            raise UnprocessableDocumentError(f"the PDF cannot be read: {error}") from None
        try:
            return len(document)
        finally:
            document.close()

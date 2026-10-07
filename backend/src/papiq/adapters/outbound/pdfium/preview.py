"""Previews of the first page with PDFium. Rendering one page takes milliseconds, so it runs in
a thread; PDFium is not thread-safe, so one page is rendered at a time."""

import asyncio
import threading
import uuid
from pathlib import Path

import pypdfium2
from PIL import Image

from papiq.core.domain.errors import UnprocessableDocumentError

DEFAULT_WIDTH = 400
_QUALITY = 80
_PDFIUM = threading.Lock()  # one PDFium library per process


class PdfiumPreviewRenderer:
    def __init__(self, *, width: int = DEFAULT_WIDTH) -> None:
        self._width = width

    async def render_first_page(self, source: Path, target: Path) -> None:
        await asyncio.to_thread(self._render, source, target)

    def _render(self, source: Path, target: Path) -> None:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with _PDFIUM:
                image = _first_page(source, self._width)
            image.save(temporary, "WEBP", quality=_QUALITY)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def _first_page(source: Path, width: int) -> Image.Image:
    try:
        document = pypdfium2.PdfDocument(source)
    except pypdfium2.PdfiumError as error:
        raise UnprocessableDocumentError(f"the PDF cannot be read: {error}") from None
    try:
        if len(document) == 0:
            raise UnprocessableDocumentError("the PDF has no pages")
        page = document[0]
        try:
            bitmap = page.render(scale=width / page.get_width())
            image: Image.Image = bitmap.to_pil().convert("RGB")
            if image.width != width:  # PDFium rounds the bitmap size up
                image = image.resize((width, round(image.height * width / image.width)))
            return image
        finally:
            page.close()
    finally:
        document.close()

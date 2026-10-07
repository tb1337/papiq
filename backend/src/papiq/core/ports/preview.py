from pathlib import Path
from typing import Protocol

PREVIEW_MEDIA_TYPE = "image/webp"


class PreviewRenderer(Protocol):
    """Renders preview images of documents. First adapter: PDFium."""

    async def render_first_page(self, source: Path, target: Path) -> None:
        """Write the first page of the PDF `source` to `target` as a WebP image of a fixed
        width. UnprocessableDocumentError if the PDF cannot be read; then `target` is not
        created."""
        ...

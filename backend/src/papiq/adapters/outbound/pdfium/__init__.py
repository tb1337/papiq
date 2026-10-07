"""Preview adapter: PDFium (pypdfium2) renders, Pillow encodes WebP."""

from papiq.adapters.outbound.pdfium.library import count_pages
from papiq.adapters.outbound.pdfium.preview import PdfiumPreviewRenderer

__all__ = ["PdfiumPreviewRenderer", "count_pages"]

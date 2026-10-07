"""The PDFium preview renderer passes the contract and keeps the width."""

from pathlib import Path

import pytest
from PIL import Image

from papiq.adapters.outbound.pdfium import PdfiumPreviewRenderer
from tests.contracts.processing import SAMPLES, PreviewRendererContract


@pytest.fixture
def preview_renderer() -> PdfiumPreviewRenderer:
    return PdfiumPreviewRenderer()


class TestPdfiumPreviewRenderer(PreviewRendererContract):
    pass


async def test_the_preview_has_the_configured_width(tmp_path: Path) -> None:
    target = tmp_path / "preview.webp"
    await PdfiumPreviewRenderer(width=200).render_first_page(SAMPLES / "text.pdf", target)
    with Image.open(target) as image:
        assert image.format == "WEBP"
        assert image.width == 200
        assert image.height == round(200 * 842 / 595)  # A4 portrait

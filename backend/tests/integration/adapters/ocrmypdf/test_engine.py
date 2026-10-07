"""OCRmyPDF with Tesseract and Ghostscript: passes the contract and recognises text. Skips if
the programs are not installed (the devcontainer has them)."""

import shutil
from datetime import timedelta
from pathlib import Path

import pypdfium2
import pytest

from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.core.domain import media_types
from tests.contracts.processing import SAMPLES, OcrContract

if not (shutil.which("tesseract") and shutil.which("gs")):
    pytest.skip("Tesseract or Ghostscript not installed", allow_module_level=True)


def engine(timeout: timedelta = timedelta(minutes=2)) -> OcrmypdfEngine:
    return OcrmypdfEngine(languages=["deu", "eng"], timeout=timeout)


@pytest.fixture
def ocr() -> OcrmypdfEngine:
    return engine()


class TestOcrmypdfEngine(OcrContract):
    pass


def text_of(pdf: Path) -> str:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return " ".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


@pytest.mark.parametrize(
    ("sample", "media_type"),
    [("scan.pdf", media_types.PDF), ("photo.jpg", media_types.JPEG)],
)
async def test_scans_and_photos_get_a_text_layer(
    tmp_path: Path, sample: str, media_type: str
) -> None:
    target = tmp_path / "archive.pdf"
    assert "Rechnung" not in text_of(SAMPLES / "scan.pdf")
    result = await engine().make_archive(SAMPLES / sample, target, media_type=media_type)
    text = " ".join(text_of(target).split())
    assert "Rechnung Nummer 4711" in text
    assert "123,45 EUR" in text
    assert result.pdfa
    assert result.engine.startswith("ocrmypdf ")
    assert "tesseract" in result.engine


async def test_existing_text_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "archive.pdf"
    await engine().make_archive(SAMPLES / "text.pdf", target, media_type=media_types.PDF)
    assert text_of(target).split() == text_of(SAMPLES / "text.pdf").split()


async def test_an_empty_page_has_no_text(tmp_path: Path) -> None:
    target = tmp_path / "archive.pdf"
    result = await engine().make_archive(SAMPLES / "blank.pdf", target, media_type=media_types.PDF)
    assert result.pages == 1
    assert text_of(target).strip() == ""


async def test_the_time_limit_stops_ocr(tmp_path: Path) -> None:
    target = tmp_path / "archive.pdf"
    with pytest.raises(TimeoutError, match="did not finish"):
        await engine(timeout=timedelta(milliseconds=50)).make_archive(
            SAMPLES / "scan.pdf", target, media_type=media_types.PDF
        )
    assert list(tmp_path.iterdir()) == []

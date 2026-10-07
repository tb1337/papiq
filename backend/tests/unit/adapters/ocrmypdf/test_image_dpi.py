"""Images are prepared for OCRmyPDF: a plausible resolution, no transparency, all pages."""

from pathlib import Path

import pytest
from PIL import Image, ImageSequence

from papiq.adapters.outbound.ocrmypdf.engine import image_dpi, prepare_image
from papiq.core.domain.errors import UnprocessableDocumentError
from tests.contracts.processing import SAMPLES


@pytest.mark.parametrize(
    ("size", "stated", "expected"),
    [
        ((2480, 3508), (300, 300), 300),  # A4 at 300 dpi: kept
        ((1200, 900), (72, 72), 72),  # 16.7 inches at 72 dpi: plausible, kept
        ((4032, 3024), (72, 72), 345),  # phone photo claiming 72 dpi: 56 inches
        ((1000, 1300), None, 111),  # no resolution
        ((600, 300), (600, 600), 51),  # one inch wide: too small
    ],
)
def test_images_get_a_plausible_resolution(
    size: tuple[int, int], stated: tuple[int, int] | None, expected: int
) -> None:
    assert image_dpi(size, stated) == expected


def test_a_usable_image_is_passed_on_as_it_is(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared.tiff"
    assert prepare_image(SAMPLES / "lowres.jpg", prepared) == (SAMPLES / "lowres.jpg", 72)
    assert not prepared.exists()


def test_transparency_is_put_on_white(tmp_path: Path) -> None:
    path, dpi = prepare_image(SAMPLES / "screenshot.png", tmp_path / "prepared.tiff")
    assert (path, dpi) == (tmp_path / "prepared.tiff", 150)
    with Image.open(path) as image:
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_all_pages_of_a_tiff_are_kept(tmp_path: Path) -> None:
    path, _ = prepare_image(SAMPLES / "pages.tiff", tmp_path / "prepared.tiff")
    with Image.open(path) as image:
        assert len(list(ImageSequence.Iterator(image))) == 2


def test_cmyk_becomes_rgb(tmp_path: Path) -> None:
    source = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (800, 1100), (0, 0, 0, 0)).save(source, dpi=(100, 100))
    path, _ = prepare_image(source, tmp_path / "prepared.tiff")
    with Image.open(path) as image:
        assert image.mode == "RGB"


def test_an_unreadable_image_is_unprocessable(tmp_path: Path) -> None:
    source = tmp_path / "broken.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    with pytest.raises(UnprocessableDocumentError, match="cannot be read"):
        prepare_image(source, tmp_path / "prepared.tiff")

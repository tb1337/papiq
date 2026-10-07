"""Images get a plausible page size before OCR."""

from pathlib import Path

import pytest
from PIL import Image

from papiq.adapters.outbound.ocrmypdf.engine import image_dpi


@pytest.mark.parametrize(
    ("size", "dpi", "expected"),
    [
        ((2480, 3508), (300, 300), None),  # A4 at 300 dpi: kept
        ((4032, 3024), (72, 72), 345),  # phone photo claiming 72 dpi: 56 inches
        ((1000, 1300), None, 111),  # no resolution
        ((600, 300), (600, 600), 51),  # one inch wide: too small
    ],
)
def test_images_get_a_plausible_page_size(
    tmp_path: Path, size: tuple[int, int], dpi: tuple[int, int] | None, expected: int | None
) -> None:
    path = tmp_path / "image.png"
    image = Image.new("L", size, 255)
    if dpi:
        image.save(path, dpi=dpi)
    else:
        image.save(path)
    assert image_dpi(path) == expected

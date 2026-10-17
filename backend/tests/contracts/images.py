"""Contract suite for `ImageNormaliser`; needs the fixture `image_normaliser`."""

import io

import pytest
from PIL import Image

from papiq.core.domain import media_types
from papiq.core.domain.errors import ImageError, UnsupportedMediaTypeError
from papiq.core.ports.images import LOGO_SIZE, ImageNormaliser


def image(
    size: tuple[int, int], format: str = "PNG", colour: tuple[int, ...] = (13, 107, 107, 128)
) -> bytes:
    """A picture of one colour, as `format`."""
    mode = "RGB" if format == "JPEG" else "RGBA"
    out = io.BytesIO()
    Image.new(mode, size, colour[: len(mode)]).save(out, format)
    return out.getvalue()


LOGO_PNG = image((180, 180))
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class ImageNormaliserContract:
    @pytest.mark.parametrize(
        "data",
        [image((300, 150)), image((40, 40), "WEBP"), image((200, 200), "JPEG"), LOGO_PNG],
        ids=["png", "webp", "jpeg", "square"],
    )
    async def test_makes_a_small_webp(self, image_normaliser: ImageNormaliser, data: bytes) -> None:
        logo = await image_normaliser.normalise(data)
        assert media_types.detect_image(logo) == media_types.WEBP
        with Image.open(io.BytesIO(logo)) as made:
            assert max(made.size) <= LOGO_SIZE

    async def test_keeps_transparency(self, image_normaliser: ImageNormaliser) -> None:
        logo = await image_normaliser.normalise(image((300, 300), colour=(255, 0, 0, 100)))
        with Image.open(io.BytesIO(logo)) as made:
            assert made.mode == "RGBA"
            assert made.getpixel((0, 0))[3] < 255  # type: ignore[index]

    async def test_reads_an_icon_file(self, image_normaliser: ImageNormaliser) -> None:
        out = io.BytesIO()
        Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(out, "ICO", sizes=[(16, 16), (64, 64)])
        logo = await image_normaliser.normalise(out.getvalue())
        assert media_types.detect_image(logo) == media_types.WEBP

    @pytest.mark.parametrize("data", [SVG, b"GIF89a" + bytes(100), b"%PDF-1.7\n" + bytes(100)])
    async def test_refuses_other_kinds(
        self, image_normaliser: ImageNormaliser, data: bytes
    ) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            await image_normaliser.normalise(data)

    async def test_a_damaged_image_cannot_be_read(self, image_normaliser: ImageNormaliser) -> None:
        with pytest.raises(ImageError):
            await image_normaliser.normalise(LOGO_PNG[:40])

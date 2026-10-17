"""A stand-in for making logos: no image decoding."""

from papiq.core.domain import media_types
from papiq.core.domain.errors import ImageError, UnsupportedMediaTypeError

FAKE_LOGO = (
    b"RIFF\x1e\x00\x00\x00WEBPVP8L\x11\x00\x00\x00/\x00\x00\x00\x10\x07\xd0\xb56t\xad\x80"
    b"\x81\x88\xe8\x7f\x00\x00"
)
"""A WebP of one half-transparent petrol pixel: every logo the fake normaliser makes."""
_DAMAGED = 64  # bytes: an image no longer than this counts as cut off


class FakeImageNormaliser:
    """Any image of a kind a logo is made from becomes `FAKE_LOGO`; one of at most 64 bytes
    counts as damaged. `calls` counts the images it was given."""

    def __init__(self) -> None:
        self.calls = 0

    async def normalise(self, data: bytes) -> bytes:
        self.calls += 1
        if media_types.detect_image(data[: media_types.SNIFF_SIZE]) is None:
            raise UnsupportedMediaTypeError("a logo is a PNG, JPEG, WebP or ICO image")
        if len(data) <= _DAMAGED:
            raise ImageError("the image cannot be read")
        return FAKE_LOGO

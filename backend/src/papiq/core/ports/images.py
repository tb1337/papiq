"""Making a contact's logo from an image."""

from typing import Protocol

LOGO_SIZE = 128
"""Pixels a logo fits into, square."""


class ImageNormaliser(Protocol):
    """Reads a raster image and makes it a logo. First adapter: Pillow.

    The image comes from an admin or from a website (`LogoFetcher`), so it is not trusted:
    adapters read only the kinds named below, check its size before they decode it, and run
    outside the event loop (in a thread)."""

    async def normalise(self, data: bytes) -> bytes:
        """`data`, a PNG, JPEG, WebP or ICO image (of an ICO its largest picture), scaled down
        to fit `LOGO_SIZE` pixels square keeping its proportions, as WebP keeping its
        transparency; a smaller one keeps its size. UnsupportedMediaTypeError for another kind
        (SVG among them); ImageError if it cannot be read or would be too large decoded."""
        ...

"""Logos with Pillow: an untrusted PNG, JPEG, WebP or ICO image scaled down to a small WebP.

- Only these four decoders are asked (`Image.open(..., formats=...)`); the kind is recognised by
  the content first, so an SVG, which can carry scripts, never reaches one.
- The size is checked before anything is decoded: at most `MAX_PIXELS` pixels. A small file can
  declare a huge picture (a decompression bomb). Of an ICO (at most `MAX_ICON_ENTRIES`
  pictures), every picture is opened on its own, which reads only its header, and checked as
  Pillow will read it (a PNG by its last IHDR, a bitmap by its DIB header), since opening the
  ICO decodes its largest picture at once.
- A progressive JPEG is decoded scan by scan, each over the whole picture: one of many scans
  under 1 MB would bind a CPU for minutes. More than `MAX_JPEG_SCANS` start-of-scan markers are
  refused before decoding. They are counted over the whole file: the coded data holds none (a
  0xFF there is always followed by 0x00 or a restart marker), metadata may (an EXIF thumbnail),
  which the limit leaves room for. A JPEG is decoded at a reduced scale where it can be
  (`draft`) and turned upright by its EXIF orientation.
- Transparency stays (RGBA); the result fits `LOGO_SIZE` pixels square.
- Whatever Pillow raises for such a file is an ImageError (it raises more than its documented
  errors for broken metadata); EXIF it cannot apply leaves the picture as it is.
"""

import asyncio
import io

from PIL import BmpImagePlugin, Image, ImageOps

from papiq.core.domain import media_types
from papiq.core.domain.errors import ImageError, UnsupportedMediaTypeError
from papiq.core.ports.images import LOGO_SIZE

MAX_PIXELS = 4096 * 4096
"""Pixels of an image that is decoded at all (64 MB as RGBA)."""
MAX_JPEG_SCANS = 64
"""Scans of a progressive JPEG (an encoder writes about ten)."""
MAX_ICON_ENTRIES = 64
"""Pictures of an ICO (a website's favicon has a few)."""
_FORMATS = ("PNG", "JPEG", "WEBP", "ICO")
_PNG = b"\x89PNG\r\n\x1a\n"

_QUALITY = 90


class PillowImageNormaliser:
    def __init__(self, *, size: int = LOGO_SIZE, max_pixels: int = MAX_PIXELS) -> None:
        self._size = size
        self._max_pixels = max_pixels

    async def normalise(self, data: bytes) -> bytes:
        return await asyncio.to_thread(self._normalise, data)

    def _normalise(self, data: bytes) -> bytes:
        kind = media_types.detect_image(data[: media_types.SNIFF_SIZE])
        if kind is None:
            raise UnsupportedMediaTypeError("a logo is a PNG, JPEG, WebP or ICO image")
        if kind == media_types.JPEG and data.count(b"\xff\xda") > MAX_JPEG_SCANS:
            raise ImageError(f"the image has more than {MAX_JPEG_SCANS} scans")
        try:
            if kind == media_types.ICO:
                self._check_icon(data)
            with Image.open(io.BytesIO(data), formats=_FORMATS) as image:
                return self._logo(image)
        except ImageError:
            raise
        except Exception:  # an untrusted file: Pillow's decoders raise all kinds of errors
            raise ImageError("the image cannot be read") from None

    def _logo(self, image: Image.Image) -> bytes:
        self._check_size(*image.size)
        if image.format == "JPEG":
            image.draft("RGB", (self._size, self._size))
        image.load()
        try:
            picture = ImageOps.exif_transpose(image)
        except Exception:  # EXIF it cannot read (a resolution as text): the picture as it is
            picture = image
        transparent = picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info
        picture = picture.convert("RGBA" if transparent else "RGB")
        picture.thumbnail((self._size, self._size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        picture.save(out, "WEBP", quality=_QUALITY, method=4)
        return out.getvalue()

    def _check_size(self, width: int, height: int) -> None:
        if width < 1 or height < 1:
            raise ImageError("the image has no pixels")
        if width * height > self._max_pixels:
            raise ImageError(f"the image is larger than {self._max_pixels} pixels")

    def _check_icon(self, data: bytes) -> None:
        """Every picture of an ICO by the size Pillow reads from its own header (a PNG's
        last IHDR, a bitmap's DIB header), which may differ from the ICO's directory; opening
        a picture on its own reads no more than its header."""
        count = int.from_bytes(data[4:6], "little")
        if not 0 < count <= MAX_ICON_ENTRIES or len(data) < 6 + 16 * count:
            raise ImageError(f"an icon holds 1 to {MAX_ICON_ENTRIES} pictures")
        for index in range(count):
            entry = data[6 + 16 * index : 6 + 16 * (index + 1)]
            size = int.from_bytes(entry[8:12], "little")
            offset = int.from_bytes(entry[12:16], "little")
            if size < 24 or offset + size > len(data):
                raise ImageError("the image cannot be read")
            picture = io.BytesIO(data[offset : offset + size])
            if data.startswith(_PNG, offset):
                with Image.open(picture, formats=["PNG"]) as frame:
                    width, height = frame.size
            else:  # a bitmap's height counts its mask too
                with BmpImagePlugin.DibImageFile(picture) as frame:
                    width, height = frame.size[0], frame.size[1] // 2
            self._check_size(width, height)

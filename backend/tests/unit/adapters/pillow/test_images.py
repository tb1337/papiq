"""Pillow makes logos: passes the contract, checks the size before it decodes (also every
picture of an ICO), keeps proportions, transparency and orientation."""

import io
import struct
import time
import zlib

import pytest
from PIL import Image, ImageOps

from papiq.adapters.outbound.pillow import PillowImageNormaliser
from papiq.adapters.outbound.pillow.images import MAX_JPEG_SCANS
from papiq.core.domain.errors import ImageError, UnsupportedMediaTypeError
from tests.contracts.images import ImageNormaliserContract, image


@pytest.fixture
def image_normaliser() -> PillowImageNormaliser:
    return PillowImageNormaliser()


class TestPillowImageNormaliser(ImageNormaliserContract):
    pass


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body)
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def png_claiming(width: int, height: int, *, decoy: bool = False) -> bytes:
    """A PNG whose header claims `width` by `height` grey pixels, with one row of data: tiny,
    but a bomb if it were decoded. `decoy`: a first IHDR of one pixel before the real one
    (Pillow takes the last)."""
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    first = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)) if decoy else b""
    data = zlib.compress(b"\x00" * (width + 1))
    chunks = first + chunk(b"IHDR", header) + chunk(b"IDAT", data) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + chunks


def icon_of(*pictures: bytes) -> bytes:
    """An ICO holding `pictures` as they are, its directory saying 16 by 16 for each."""
    offset = 6 + 16 * len(pictures)
    directory = struct.pack("<HHH", 0, 1, len(pictures))
    for picture in pictures:
        directory += struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, len(picture), offset)
        offset += len(picture)
    return directory + b"".join(pictures)


async def test_scales_down_keeping_proportions(image_normaliser: PillowImageNormaliser) -> None:
    assert opened(await image_normaliser.normalise(image((300, 150)))).size == (128, 64)
    assert opened(await image_normaliser.normalise(image((90, 600), "JPEG"))).size == (19, 128)
    # A small one keeps its size.
    assert opened(await image_normaliser.normalise(image((40, 30), "WEBP"))).size == (40, 30)


async def test_keeps_transparency_and_opaque_stays_without_alpha(
    image_normaliser: PillowImageNormaliser,
) -> None:
    clear = opened(await image_normaliser.normalise(image((200, 200), colour=(1, 2, 3, 0))))
    assert clear.mode == "RGBA" and clear.getpixel((10, 10))[3] == 0  # type: ignore[index]
    photo = opened(await image_normaliser.normalise(image((200, 200), "JPEG")))
    assert photo.mode == "RGB"


async def test_turns_a_photo_upright(image_normaliser: PillowImageNormaliser) -> None:
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotated by 90°
    Image.new("RGB", (200, 100), (200, 30, 30)).save(out, "JPEG", exif=exif)
    assert opened(await image_normaliser.normalise(out.getvalue())).size == (64, 128)


async def test_takes_the_largest_picture_of_an_icon(
    image_normaliser: PillowImageNormaliser,
) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (256, 256), (0, 0, 255, 200)).save(
        out, "ICO", sizes=[(16, 16), (32, 32), (256, 256)]
    )
    logo = opened(await image_normaliser.normalise(out.getvalue()))
    assert logo.size == (128, 128) and logo.mode == "RGBA"


async def test_refuses_a_bomb_before_decoding_it(image_normaliser: PillowImageNormaliser) -> None:
    bomb = png_claiming(5000, 5000)  # 25 million pixels in a few kilobytes
    assert len(bomb) < 10_000
    with pytest.raises(ImageError, match="larger than"):
        await image_normaliser.normalise(bomb)
    with pytest.raises(ImageError):
        await PillowImageNormaliser(max_pixels=100).normalise(image((20, 20)))
    with pytest.raises(ImageError):
        await image_normaliser.normalise(png_claiming(0, 10))


async def test_refuses_an_icon_whose_picture_claims_more_than_its_directory(
    image_normaliser: PillowImageNormaliser,
) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 0, 255, 200)).save(out, "ICO", sizes=[(16, 16), (64, 64)])
    data = bytearray(out.getvalue())
    # The second entry's PNG says 60000 by 60000 in its header; the directory still says 64.
    offset = int.from_bytes(data[6 + 16 + 12 : 6 + 16 + 16], "little")
    assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
    data[offset + 16 : offset + 24] = struct.pack(">II", 60000, 60000)
    with pytest.raises(ImageError):
        await image_normaliser.normalise(bytes(data))
    # A bitmap picture claiming as much (its height counts its mask twice).
    header = struct.pack("<IiiHHI", 40, 50000, 100000, 1, 32, 0) + bytes(16)
    directory = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, 40, 22)
    with pytest.raises(ImageError):
        await image_normaliser.normalise(directory + header)
    # A directory pointing past the end of the file.
    broken = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 999, 22)
    with pytest.raises(ImageError):
        await image_normaliser.normalise(broken + bytes(30))


async def test_refuses_an_icon_whose_picture_hides_its_size_behind_a_decoy(
    image_normaliser: PillowImageNormaliser,
) -> None:
    started = time.monotonic()
    with pytest.raises(ImageError, match="larger than"):
        await image_normaliser.normalise(
            icon_of(image((16, 16)), png_claiming(9000, 9000, decoy=True))
        )
    assert time.monotonic() - started < 0.5  # refused before anything was decoded
    with pytest.raises(ImageError, match="pictures"):
        await image_normaliser.normalise(icon_of(*[image((16, 16))] * 65))
    with pytest.raises(ImageError, match="pictures"):
        await image_normaliser.normalise(icon_of())


def bad_exif() -> bytes:
    """A JPEG rotated by its EXIF, which also gives the resolution as text: Pillow's
    `exif_transpose` raises a TypeError for it."""
    out = io.BytesIO()
    Image.new("RGB", (80, 40), (200, 30, 30)).save(out, "JPEG")
    data = out.getvalue()
    entries = struct.pack(">HHI4s", 0x011B, 2, 4, b"Cam\x00")  # YResolution as text
    entries += struct.pack(">HHIHH", 0x0112, 3, 1, 6, 0)  # orientation: rotated by 90°
    tiff = b"MM\x00*" + struct.pack(">IH", 8, 2) + entries + struct.pack(">I", 0)
    app1 = b"\xff\xe1" + struct.pack(">H", len(tiff) + 8) + b"Exif\x00\x00" + tiff
    return data[:2] + app1 + data[2:]


async def test_exif_it_cannot_apply_leaves_the_picture_as_it_is(
    image_normaliser: PillowImageNormaliser,
) -> None:
    with Image.open(io.BytesIO(bad_exif())) as image, pytest.raises(TypeError):
        ImageOps.exif_transpose(image)
    assert opened(await image_normaliser.normalise(bad_exif())).size == (80, 40)


async def test_whatever_breaks_in_pillow_is_an_image_error(
    image_normaliser: PillowImageNormaliser, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise struct.error("unpack requires a buffer of 4 bytes")

    data = image((40, 40))
    monkeypatch.setattr(Image.Image, "load", broken)
    with pytest.raises(ImageError):
        await image_normaliser.normalise(data)


def progressive(scans: int) -> bytes:
    """A grey progressive JPEG of 4096 by 4096 pixels with `scans` scans: the last one repeated,
    as a hostile file would, staying far below 1 MB."""
    out = io.BytesIO()
    Image.new("L", (4096, 4096), 128).save(out, "JPEG", progressive=True)
    data = out.getvalue()
    last = data.rindex(b"\xff\xda")
    extra = scans - data.count(b"\xff\xda")
    return data[:-2] + data[last:-2] * extra + data[-2:]


async def test_refuses_a_jpeg_of_many_scans_before_decoding_it(
    image_normaliser: PillowImageNormaliser,
) -> None:
    bomb = progressive(29_000)
    assert len(bomb) < 1024 * 1024
    started = time.monotonic()
    with pytest.raises(ImageError, match="scans"):
        await image_normaliser.normalise(bomb)
    assert time.monotonic() - started < 0.5
    # As many as an encoder writes, and some more, are a logo.
    assert opened(await image_normaliser.normalise(progressive(MAX_JPEG_SCANS))).size == (
        128,
        128,
    )


@pytest.mark.parametrize(
    "data",
    [
        b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>",
        b"GIF89a" + bytes(200),
        b"\x00\x00\x02\x00" + bytes(200),  # a cursor, not an icon
        b"",
    ],
)
async def test_refuses_what_a_logo_is_not_made_from(
    image_normaliser: PillowImageNormaliser, data: bytes
) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await image_normaliser.normalise(data)


async def test_a_damaged_image_cannot_be_read(image_normaliser: PillowImageNormaliser) -> None:
    whole = image((100, 100), "JPEG")
    for broken in (whole[:300], image((100, 100))[:-30], b"RIFF\x00\x00\x00\x00WEBPVP8 junk"):
        with pytest.raises(ImageError):
            await image_normaliser.normalise(broken)

"""File types Papiq accepts, recognised by their content, not by name or declared type."""

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
TIFF = "image/tiff"
WEBP = "image/webp"
ICO = "image/x-icon"

SUPPORTED: tuple[str, ...] = (PDF, JPEG, PNG, TIFF)
"""PDFs and photos or scans as images. Office files and e-mails are not supported yet."""

SNIFF_SIZE = 1024
"""Bytes from the start of a file that `detect` needs."""

_SIGNATURES = (
    (b"\xff\xd8\xff", JPEG),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"II*\x00", TIFF),
    (b"MM\x00*", TIFF),
)


LOGO_UPLOADS: tuple[str, ...] = (PNG, JPEG, WEBP)
"""Images an admin uploads as a contact's logo; SVG is none of them (it can carry scripts)."""
LOGO_IMAGES: tuple[str, ...] = (*LOGO_UPLOADS, ICO)
"""Images a logo is made from: the uploads and the icons of websites (`favicon.ico`)."""


def detect_image(head: bytes) -> str | None:
    """The media type of a raster image a logo is made from (`LOGO_IMAGES`) starting with
    `head` (at least its first 12 bytes), or None."""
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"\x00\x00\x01\x00"):
        return ICO
    found = detect(head)
    return found if found in (PNG, JPEG) else None


def detect(head: bytes) -> str | None:
    """The supported media type of a file starting with `head` (its first `SNIFF_SIZE`
    bytes), or None. PDF readers accept the `%PDF-` header anywhere in the first kilobyte."""
    for signature, media_type in _SIGNATURES:
        if head.startswith(signature):
            return media_type
    if b"%PDF-" in head[:SNIFF_SIZE]:
        return PDF
    return None

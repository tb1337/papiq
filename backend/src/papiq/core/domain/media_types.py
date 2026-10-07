"""File types Papiq accepts, recognised by their content, not by name or declared type."""

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
TIFF = "image/tiff"

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


def detect(head: bytes) -> str | None:
    """The supported media type of a file starting with `head` (its first `SNIFF_SIZE`
    bytes), or None. PDF readers accept the `%PDF-` header anywhere in the first kilobyte."""
    for signature, media_type in _SIGNATURES:
        if head.startswith(signature):
            return media_type
    if b"%PDF-" in head[:SNIFF_SIZE]:
        return PDF
    return None

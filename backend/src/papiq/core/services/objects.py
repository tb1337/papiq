"""Object keys of originals and derivatives in the object store."""

from papiq.core.domain.documents import Sha256
from papiq.core.domain.ids import DocumentId


def original_key(sha256: Sha256) -> str:
    """An original; identical content is stored once, for all owners."""
    return f"originals/{sha256.hex}"


def archive_key(document: DocumentId) -> str:
    """The archive PDF (PDF/A with text layer)."""
    return f"documents/{document}/archive.pdf"


def preview_key(document: DocumentId) -> str:
    """The preview image of the first page."""
    return f"documents/{document}/preview.webp"


def markdown_key(document: DocumentId) -> str:
    """The parsed text as Markdown."""
    return f"documents/{document}/content.md"


def structure_key(document: DocumentId) -> str:
    """The parsed structure, in the parser's JSON format."""
    return f"documents/{document}/content.json"

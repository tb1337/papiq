"""Object keys of originals and derivatives in the object store."""

from papiq.core.domain.documents import Sha256
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.ids import ContactId, DocumentId
from papiq.core.domain.pipeline import Node
from papiq.core.ports.object_store import ObjectStore

EXCHANGE_NODES = (Node.OCR, Node.SIGNAL_LLM, Node.FIELDS)
"""The nodes that ask a language model and keep what they sent (`exchanges_key`): OCR when the
vision language model reads the pages."""
LEGACY_EXCHANGES = {Node.SIGNAL_LLM: "classify", Node.FIELDS: "extract_fields"}
"""The names their files had before 0.2.0 (`classify` asked the model what the signal asks)."""


def original_key(sha256: Sha256) -> str:
    """An original; identical content is stored once, for all owners."""
    return f"originals/{sha256.hex}"


def archive_key(document: DocumentId) -> str:
    """The archive PDF (PDF/A with text layer)."""
    return f"documents/{document}/archive.pdf"


def preview_key(document: DocumentId) -> str:
    """The preview image of the first page."""
    return f"documents/{document}/preview.webp"


def page_key(document: DocumentId, number: int) -> str:
    """The image of page `number` (from 1)."""
    return f"documents/{document}/pages/{number}.webp"


def text_key(document: DocumentId) -> str:
    """The text read from the archive: plain text as it is set, pages apart by form feeds."""
    return f"documents/{document}/content.txt"


def legacy_text_key(document: DocumentId) -> str:
    """Where a document parsed before 0.2 keeps its text (Docling's Markdown) until it is read
    again (`reparse`)."""
    return f"documents/{document}/content.md"


def structure_key(document: DocumentId) -> str:
    """The structure of the text: its pages, lines and words with their boxes (JSON)."""
    return f"documents/{document}/content.json"


def codes_key(document: DocumentId) -> str:
    """The codes read from the archive's pages (`domain.codes`); gone with a new archive."""
    return f"documents/{document}/codes.json"


async def load_text(store: ObjectStore, document: DocumentId) -> bytes:
    """The document's text, or the Markdown of one parsed before 0.2. NotFoundError if it has
    neither (it is not parsed yet)."""
    try:
        return await store.get(text_key(document))
    except NotFoundError:
        return await store.get(legacy_text_key(document))


def exchanges_key(document: DocumentId, node: Node) -> str:
    """What `node` sent to the language model and got back, every run (`domain.exchanges`)."""
    return f"documents/{document}/exchanges/{node.value}.json"


def logo_key(contact: ContactId) -> str:
    """The logo of a contact (WebP, `ports.images.LOGO_SIZE`), uploaded or fetched."""
    return f"contacts/{contact}/logo.webp"


def classifier_key(number: int) -> str:
    """The `number`-th trained classifier (outside `documents/`: not one document's)."""
    return f"classifier/model-{number}.npz"


def legacy_exchanges_key(document: DocumentId, node: Node) -> str | None:
    """Where a document processed before the nodes were renamed keeps `node`'s exchanges."""
    name = LEGACY_EXCHANGES.get(node)
    return None if name is None else f"documents/{document}/exchanges/{name}.json"


def derivative_keys(document: DocumentId, page_count: int | None = None) -> list[str]:
    """Everything the pipeline stores for one document with `page_count` page images."""
    legacy = (legacy_exchanges_key(document, node) for node in LEGACY_EXCHANGES)
    return [
        archive_key(document),
        preview_key(document),
        text_key(document),
        legacy_text_key(document),
        structure_key(document),
        codes_key(document),
        *(exchanges_key(document, node) for node in EXCHANGE_NODES),
        *(key for key in legacy if key is not None),
        *(page_key(document, number) for number in range(1, (page_count or 0) + 1)),
    ]

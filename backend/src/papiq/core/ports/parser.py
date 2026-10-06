from typing import Protocol


class DocumentParser(Protocol):
    """Extracts structure (text, tables, layout) as Markdown or JSON. First adapter: Docling."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class ParseResult:
    pages: int
    parser: str  # name and version of the parser, for the processing log
    # Why the result is less than asked for, for the processing log: the layout analysis found
    # no text and the Markdown is the plain text layer of the archive instead.
    note: str | None = None


class DocumentParser(Protocol):
    """Extracts structure (text, tables, layout) as Markdown and JSON. First adapter: Docling.

    Parsing runs outside the event loop (adapters use processes) and is limited in time.
    """

    async def parse(self, source: Path, *, markdown: Path, structure: Path) -> ParseResult:
        """Parse the archive PDF `source` using its text layer: Markdown to `markdown`, the
        parser's lossless structure as JSON to `structure`. If the layout analysis finds no
        text although the archive has a text layer, `markdown` is that text, plain, and `note`
        says so.

        UnprocessableDocumentError if the file cannot be processed; TimeoutError if parsing
        takes too long. Other errors may be temporary. On error, neither file is created.
        """
        ...

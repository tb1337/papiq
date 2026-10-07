from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class OcrResult:
    pages: int
    engine: str  # name and version of the engine, for the processing log
    pdfa: bool  # False if the archive could not be made PDF/A; it is a plain PDF then


class Ocr(Protocol):
    """Produces a searchable archive PDF with a text layer. First adapter: OCRmyPDF.

    Recognition runs outside the event loop (adapters use processes) and is limited in time.
    """

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        """Write the archive PDF of `source` (a PDF or an image of `media_type`) to `target`:
        PDF/A with a text layer. Pages that already contain text are not recognised again;
        images become a PDF.

        UnprocessableDocumentError if the file cannot be processed (damaged, encrypted);
        repeating would not help. TimeoutError if recognition takes too long. Other errors may
        be temporary. On error, `target` is not created.
        """
        ...

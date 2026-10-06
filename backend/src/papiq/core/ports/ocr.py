from typing import Protocol


class Ocr(Protocol):
    """Produces a searchable archive PDF with a text layer. First adapter: OCRmyPDF."""

"""OCR with OCRmyPDF (Tesseract, Ghostscript), run as a child process per document.

`--skip-text` leaves pages that already have text alone; `--output-type pdfa` makes the archive
PDF/A. The output is written next to the target and renamed when complete.

The page size of an image follows from its resolution. Photos often state none, or 72 dpi,
which gives pages of several feet; tiny or huge pages are hard to read and the layout analysis
of the parser takes their text for a picture. So an image whose stated resolution gives a page
outside `PLAUSIBLE_PAGE` is scaled to the long edge of A4.

OCRmyPDF's exit codes tell permanent problems (damaged or encrypted input) from others.
"""

import asyncio
import importlib.metadata
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pypdfium2
from PIL import Image

from papiq.adapters.outbound.system import run_process
from papiq.core.domain import media_types
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports.ocr import OcrResult

log = logging.getLogger(__name__)

# ocrmypdf.ExitCode
_INPUT_FILE = 2
_ALREADY_DONE_OCR = 6
_ENCRYPTED_PDF = 8
_PDFA_CONVERSION_FAILED = 10
_UNPROCESSABLE = {
    _INPUT_FILE: "the file is damaged or not a valid document",
    _ALREADY_DONE_OCR: "the file cannot be processed (e.g. digitally signed)",
    _ENCRYPTED_PDF: "the PDF is encrypted",
}

A4_LONG_EDGE = 11.69  # inches
PLAUSIBLE_PAGE = (3.0, 17.0)  # long edge in inches: from a receipt to A3


class OcrmypdfEngine:
    def __init__(
        self,
        *,
        languages: Sequence[str],
        timeout: timedelta,
        jobs: int = 1,
    ) -> None:
        """`languages`: Tesseract language codes, e.g. `deu`, `eng`. `jobs`: CPU cores for
        the pages of one document."""
        if not languages:
            raise ValueError("at least one OCR language is needed")
        self._languages = "+".join(languages)
        self._timeout = timeout
        self._jobs = jobs
        self._engine: str | None = None

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp.pdf")
        args = [
            sys.executable,
            "-m",
            "ocrmypdf",
            "--quiet",
            "--skip-text",
            "--output-type",
            "pdfa",
            "--rotate-pages",
            "--deskew",
            "--language",
            self._languages,
            "--jobs",
            str(self._jobs),
        ]
        if media_type != media_types.PDF:
            dpi = await asyncio.to_thread(image_dpi, source)
            if dpi is not None:
                args += ["--image-dpi", str(dpi)]
        try:
            completed = await run_process(
                [*args, str(source), str(temporary)], timeout=self._timeout
            )
            code = completed.returncode
            if code in _UNPROCESSABLE:
                raise UnprocessableDocumentError(
                    f"{_UNPROCESSABLE[code]}: {_last_line(completed.stderr)}"
                )
            if code not in (0, _PDFA_CONVERSION_FAILED):
                raise RuntimeError(
                    f"ocrmypdf failed with exit code {code}: {_last_line(completed.stderr)}"
                )
            if code == _PDFA_CONVERSION_FAILED:
                log.warning("archive is not PDF/A", extra={"source": str(source)})
            pages = await asyncio.to_thread(_count_pages, temporary)
            await asyncio.to_thread(temporary.replace, target)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
        return OcrResult(
            pages=pages, engine=await self._engine_name(), pdfa=code != _PDFA_CONVERSION_FAILED
        )

    async def _engine_name(self) -> str:
        if self._engine is None:
            tesseract = await run_process(["tesseract", "--version"], timeout=timedelta(seconds=30))
            first = (tesseract.stdout or tesseract.stderr).strip().splitlines()
            self._engine = (
                f"ocrmypdf {importlib.metadata.version('ocrmypdf')}, "
                f"{first[0] if first else 'tesseract (unknown version)'}"
            )
        return self._engine


def image_dpi(path: Path) -> int | None:
    """The resolution to use for an image, or None to keep the one it states."""
    try:
        with Image.open(path) as image:
            long_edge = max(image.size)
            stated = image.info.get("dpi")
    except (OSError, ValueError):
        return None  # OCRmyPDF reports the problem
    if stated:
        resolution = min(float(value) for value in stated)
        if resolution > 0 and PLAUSIBLE_PAGE[0] <= long_edge / resolution <= PLAUSIBLE_PAGE[1]:
            return None
    return max(1, round(long_edge / A4_LONG_EDGE))


def _count_pages(path: Path) -> int:
    document = pypdfium2.PdfDocument(path)
    try:
        return len(document)
    finally:
        document.close()


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no details"

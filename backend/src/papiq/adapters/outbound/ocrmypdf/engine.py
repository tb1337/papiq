"""OCR with OCRmyPDF (Tesseract, Ghostscript), run as a child process per document.

`--skip-text` leaves pages that already have text alone; `--output-type pdfa` makes the archive
PDF/A. The output is written next to the target and renamed when complete.

Images are prepared before OCRmyPDF sees them (`prepare_image`):

- The page size follows from the resolution. Photos often state none, or 72 dpi, which gives
  pages of several feet; tiny or huge pages are hard to read and the layout analysis of the
  parser takes their text for a picture. An image whose stated resolution gives a page outside
  `PLAUSIBLE_PAGE` is scaled to the long edge of A4. The resolution is always passed on, since
  OCRmyPDF refuses images that state 96 dpi or less.
- OCRmyPDF refuses transparency and some colour modes: transparent images are put on white,
  CMYK and high bit depths are converted. All pages of a multi-page TIFF are kept.

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

from PIL import Image, ImageSequence

from papiq.adapters.outbound.pdfium import count_pages
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
        prepared = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp.tiff")
        try:
            if media_type != media_types.PDF:
                source, dpi = await asyncio.to_thread(prepare_image, source, prepared)
                args += ["--image-dpi", str(dpi)]
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
            pages = await asyncio.to_thread(count_pages, temporary)
            await asyncio.to_thread(temporary.replace, target)
        finally:
            for path in (temporary, prepared):
                await asyncio.to_thread(path.unlink, missing_ok=True)
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


def prepare_image(source: Path, prepared: Path) -> tuple[Path, int]:
    """The image to give OCRmyPDF and its resolution. `source` itself if it can be used as it
    is, otherwise a TIFF written to `prepared`. UnprocessableDocumentError if the image cannot
    be read."""
    try:
        with Image.open(source) as image:
            dpi = image_dpi(image.size, image.info.get("dpi"))
            frames = [frame.copy() for frame in ImageSequence.Iterator(image)]
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise UnprocessableDocumentError(f"the image cannot be read: {error}") from None
    converted = [_ocr_ready(frame) for frame in frames]
    if len(frames) == 1 and converted[0] is frames[0]:
        return source, dpi
    first, *rest = converted
    first.save(
        prepared, "TIFF", save_all=True, append_images=rest, dpi=(dpi, dpi), compression="tiff_lzw"
    )
    return prepared, dpi


def image_dpi(size: tuple[int, int], stated: object) -> int:
    """The resolution for an image of `size` pixels that states `stated` (Pillow's `dpi`)."""
    long_edge = max(size)
    if isinstance(stated, tuple) and stated:
        resolution = min(float(value) for value in stated)
        if resolution > 0 and PLAUSIBLE_PAGE[0] <= long_edge / resolution <= PLAUSIBLE_PAGE[1]:
            return max(1, round(resolution))
    return max(1, round(long_edge / A4_LONG_EDGE))


def _ocr_ready(image: Image.Image) -> Image.Image:
    """`image` itself if OCRmyPDF takes it, otherwise a converted copy."""
    if image.mode == "P" and "transparency" in image.info:
        image = image.convert("RGBA")
    if image.mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    if image.mode in ("1", "L", "RGB", "P"):
        return image
    if image.mode.startswith(("I", "F")):
        return image.convert("L")
    return image.convert("RGB")  # CMYK, YCbCr, LAB, HSV


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no details"

"""OCR with OCRmyPDF (Tesseract, Ghostscript), run as a child process per document.

`--skip-text` leaves pages that already have text alone; `--output-type pdfa` makes the archive
PDF/A. The output is written next to the target and renamed when complete.

Two things make OCRmyPDF refuse or downgrade a file, and both get a second run:

- A digitally signed PDF (exit code 2, "digital signature"): the second run passes
  `--invalidate-digital-signatures`. The original is never changed; only the archive lacks the
  signature, and `OcrResult.note` says so.
- A file whose PDF/A conversion fails outright (exit code 1, e.g. an unusual colour space):
  the second run writes a plain PDF (`--output-type pdf`); `pdfa` is False and `note` names the
  reason. Exit code 10 means OCRmyPDF itself fell back to a plain PDF (Ghostscript refused a
  construct of the file); the reason is read from its output.

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
import re
import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageSequence

from papiq.adapters.outbound.pdfium import count_pages
from papiq.adapters.outbound.system import Completed, run_process
from papiq.core.domain import media_types
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports.ocr import OcrResult

log = logging.getLogger(__name__)

# ocrmypdf.ExitCode
_BAD_ARGS = 1  # also raised for files that need other options, e.g. a colour conversion
_INPUT_FILE = 2
_ALREADY_DONE_OCR = 6
_ENCRYPTED_PDF = 8
_PDFA_CONVERSION_FAILED = 10
_UNPROCESSABLE = {
    _INPUT_FILE: "the file is damaged or not a valid document",
    _ALREADY_DONE_OCR: "the file cannot be processed",
    _ENCRYPTED_PDF: "the PDF is encrypted",
}
_SIGNATURE_HINT = "digital signature"
SIGNATURE_NOTE = (
    "the original is digitally signed; the archive does not carry the signature "
    "(the original is unchanged)"
)
# Known reasons a first run fails with exit code 1, in words for the processing log.
_KNOWN_ERRORS = {
    "ColorConversionNeededError": "the colour space of the file cannot be represented in PDF/A",
}
_ERROR_LINE = re.compile(r"^(\w+Error): ?(.*)$")
_GHOSTSCRIPT_LINE = re.compile(r"^GPL Ghostscript [\d.]+: (.*)$")
_PDFA_ISSUE = re.compile(r"conversion to PDF/A did not succeed \(issue: (.+?)\)")
_NOTE_MAX = 300

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
        prepared = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp.tiff")
        try:
            extra: list[str] = []
            if media_type != media_types.PDF:
                source, dpi = await asyncio.to_thread(prepare_image, source, prepared)
                extra = ["--image-dpi", str(dpi)]
            pdfa, notes = await self._convert(source, temporary, extra)
            pages = await asyncio.to_thread(count_pages, temporary)
            await asyncio.to_thread(temporary.replace, target)
        finally:
            for path in (temporary, prepared):
                await asyncio.to_thread(path.unlink, missing_ok=True)
        if not pdfa:
            log.warning("archive is not PDF/A", extra={"source": str(source), "notes": notes})
        return OcrResult(
            pages=pages,
            engine=await self._engine_name(),
            pdfa=pdfa,
            note=_join(notes),
        )

    async def _convert(
        self, source: Path, output: Path, extra: Sequence[str]
    ) -> tuple[bool, list[str]]:
        """Run OCRmyPDF, with a second run where it helps (see the module). Returns whether the
        output is PDF/A, and the notes for the processing log."""
        notes: list[str] = []
        invalidate = False
        completed = await self._run(source, output, extra, pdfa=True, invalidate=False)
        if completed.returncode == _INPUT_FILE and _SIGNATURE_HINT in completed.stderr:
            invalidate = True
            notes.append(SIGNATURE_NOTE)
            completed = await self._run(source, output, extra, pdfa=True, invalidate=True)
        pdfa = True
        if completed.returncode == _BAD_ARGS:
            notes.append(f"not PDF/A: {_error_reason(completed.stderr)}")
            pdfa = False
            completed = await self._run(source, output, extra, pdfa=False, invalidate=invalidate)
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
            pdfa = False
            notes.append(f"not PDF/A: {pdfa_reasons(completed.stderr)}")
        return pdfa, notes

    async def _run(
        self, source: Path, output: Path, extra: Sequence[str], *, pdfa: bool, invalidate: bool
    ) -> Completed:
        args = [
            sys.executable,
            "-m",
            "ocrmypdf",
            "-v1",  # Ghostscript's messages name the reason when PDF/A fails
            "--skip-text",
            "--output-type",
            "pdfa" if pdfa else "pdf",
            "--rotate-pages",
            "--deskew",
            "--language",
            self._languages,
            "--jobs",
            str(self._jobs),
            *extra,
        ]
        if invalidate:
            args.append("--invalidate-digital-signatures")
        return await run_process([*args, str(source), str(output)], timeout=self._timeout)

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


def pdfa_reasons(stderr: str) -> str:
    """Why OCRmyPDF (exit code 10) wrote a plain PDF, from its verbose output: Ghostscript's
    messages about PDF/A, else the issue OCRmyPDF names, else a generic word."""
    reasons: list[str] = []
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for number, line in enumerate(lines):
        match = _GHOSTSCRIPT_LINE.match(line)
        if match is None:
            continue
        message = match.group(1)
        # Ghostscript wraps a message over two lines.
        if number + 1 < len(lines) and not _GHOSTSCRIPT_LINE.match(lines[number + 1]):
            follower = lines[number + 1]
            if "PDF/A" in follower and not follower.startswith("Page "):
                message = f"{message} {follower}"
        if "PDF/A" in message and message not in reasons:
            reasons.append(message)
    if not reasons:
        issue = _PDFA_ISSUE.search(stderr)
        reasons.append(issue.group(1) if issue else "Ghostscript could not convert the file")
    return "; ".join(reasons)


def _error_reason(stderr: str) -> str:
    """The error OCRmyPDF reported, in words where known, else its first line."""
    for line in stderr.strip().splitlines():
        match = _ERROR_LINE.match(line.strip())
        if match is not None:
            name, rest = match.groups()
            return _KNOWN_ERRORS.get(name, f"{name}: {rest}".strip(": "))
    return _last_line(stderr)


def _join(notes: Sequence[str]) -> str | None:
    if not notes:
        return None
    text = "; ".join(notes)
    return text if len(text) <= _NOTE_MAX else text[: _NOTE_MAX - 1] + "…"


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

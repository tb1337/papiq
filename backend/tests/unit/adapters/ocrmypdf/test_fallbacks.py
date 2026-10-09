"""The second runs of OCRmyPDF (signed PDFs, failed PDF/A conversion) on a stand-in process:
which arguments go out, and what the result says. The real program runs in the integration
tests."""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from papiq.adapters.outbound.ocrmypdf import engine as module
from papiq.adapters.outbound.ocrmypdf.engine import SIGNATURE_NOTE, OcrmypdfEngine, pdfa_reasons
from papiq.adapters.outbound.system import Completed
from papiq.core.domain import media_types
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports.ocr import OcrResult

SIGNATURE = (
    "DigitalSignatureError: Input PDF has a digital signature. OCR would alter the document,\n"
    "invalidating the signature.\n"
)
COLOUR = (
    "ColorConversionNeededError: The input PDF has an unusual DeviceN color space that cannot "
    "be\nrepresented in PDF/A; the output may appear blank in some viewers\nsuch as Adobe "
    "Reader. Convert it to a common color space with\n--color-conversion-strategy (RGB, CMYK, "
    "or Gray), or use\n--output-type pdf to skip PDF/A conversion and retain the original\n"
    "color space.\n"
)
GHOSTSCRIPT = "\n".join(
    [
        "Postprocessing...",
        "Running: ['gs', '-dBATCH', '-dPDFA=2', '-o', '/tmp/x/pdfa.pdf', '/tmp/x/pdfa.ps']",
        "GPL Ghostscript 10.05.1 (2025-04-29)",
        "Copyright (C) 2025 Artifex Software, Inc.  All rights reserved.",
        "Processing pages 1 through 7.",
        "Page 1",
        "GPL Ghostscript 10.05.1: Setting Overprint Mode to 1",
        "not permitted in PDF/A-2, overprint mode not set",
        "Page 2",
        "GPL Ghostscript 10.05.1: Setting Overprint Mode to 1",
        "not permitted in PDF/A-2, overprint mode not set",
        "GPL Ghostscript 10.05.1: A CIDFont uses CID 0, which is not legal for PDF/A, "
        "reverting to normal PDF output.",
        "Page 3",
        "Output file is a valid PDF, but conversion to PDF/A did not succeed "
        "(issue: No PDF/A metadata in XMP)",
        "",
    ]
)


class Runs:
    """Scripted outcomes of `run_process`: one (exit code, stderr) per OCRmyPDF run; the output
    file is written when the run succeeds (0 or 10)."""

    def __init__(self, *outcomes: tuple[int, str]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[str]] = []

    async def __call__(
        self, args: Sequence[str], *, timeout: timedelta, env: object = None
    ) -> Completed:
        if args[0] == "tesseract":
            return Completed(returncode=0, stdout="tesseract 5.5.0\n", stderr="")
        self.calls.append(list(args))
        code, stderr = self.outcomes.pop(0)
        if code in (0, 10):
            Path(args[-1]).write_bytes(b"%PDF-1.7\n%%EOF\n")
        return Completed(returncode=code, stdout="", stderr=stderr)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> Runs:
    runs = Runs()
    monkeypatch.setattr(module, "run_process", runs)
    monkeypatch.setattr(module, "count_pages", lambda path: 1)
    return runs


def engine() -> OcrmypdfEngine:
    return OcrmypdfEngine(languages=["deu"], timeout=timedelta(minutes=1))


def options(call: list[str]) -> tuple[str, bool]:
    """Output type and whether signatures are invalidated."""
    return call[call.index("--output-type") + 1], "--invalidate-digital-signatures" in call


async def convert(runs: Runs, tmp_path: Path) -> tuple[OcrResult, Path]:
    target = tmp_path / "archive.pdf"
    result = await engine().make_archive(
        tmp_path / "source.pdf", target, media_type=media_types.PDF
    )
    return result, target


async def test_a_clean_run_is_pdfa_without_a_note(runs: Runs, tmp_path: Path) -> None:
    runs.outcomes = [(0, "")]
    result, target = await convert(runs, tmp_path)
    assert (result.pdfa, result.note, result.pages) == (True, None, 1)
    assert [options(call) for call in runs.calls] == [("pdfa", False)]
    assert target.exists() and [p.name for p in tmp_path.iterdir()] == ["archive.pdf"]


async def test_a_signed_pdf_is_processed_again_without_its_signature(
    runs: Runs, tmp_path: Path
) -> None:
    runs.outcomes = [(2, SIGNATURE), (0, "")]
    result, _ = await convert(runs, tmp_path)
    assert (result.pdfa, result.note) == (True, SIGNATURE_NOTE)
    assert [options(call) for call in runs.calls] == [("pdfa", False), ("pdfa", True)]


async def test_a_failed_pdfa_conversion_falls_back_to_a_plain_pdf(
    runs: Runs, tmp_path: Path
) -> None:
    runs.outcomes = [(1, COLOUR), (0, "")]
    result, _ = await convert(runs, tmp_path)
    assert result.pdfa is False
    assert result.note == "not PDF/A: the colour space of the file cannot be represented in PDF/A"
    assert [options(call) for call in runs.calls] == [("pdfa", False), ("pdf", False)]


async def test_a_signed_pdf_whose_pdfa_conversion_fails_gets_both(
    runs: Runs, tmp_path: Path
) -> None:
    runs.outcomes = [(2, SIGNATURE), (1, "SomethingElseError: odd file\n"), (0, "")]
    result, _ = await convert(runs, tmp_path)
    assert result.pdfa is False
    assert result.note == f"{SIGNATURE_NOTE}; not PDF/A: SomethingElseError: odd file"
    assert [options(call) for call in runs.calls] == [
        ("pdfa", False),
        ("pdfa", True),
        ("pdf", True),
    ]


async def test_ocrmypdfs_own_fallback_names_ghostscripts_reasons(
    runs: Runs, tmp_path: Path
) -> None:
    runs.outcomes = [(10, GHOSTSCRIPT)]
    result, _ = await convert(runs, tmp_path)
    assert result.pdfa is False
    assert result.note == (
        "not PDF/A: Setting Overprint Mode to 1 not permitted in PDF/A-2, overprint mode not "
        "set; A CIDFont uses CID 0, which is not legal for PDF/A, reverting to normal PDF output."
    )
    assert len(runs.calls) == 1


def test_pdfa_reasons_without_ghostscript_messages() -> None:
    assert pdfa_reasons(GHOSTSCRIPT.splitlines()[-1]) == "No PDF/A metadata in XMP"
    assert pdfa_reasons("nothing useful\n") == "Ghostscript could not convert the file"


async def test_other_input_errors_are_unprocessable_at_once(runs: Runs, tmp_path: Path) -> None:
    runs.outcomes = [(2, "InputFileError: not a PDF\n")]
    with pytest.raises(UnprocessableDocumentError, match="not a PDF"):
        await convert(runs, tmp_path)
    assert len(runs.calls) == 1
    assert list(tmp_path.iterdir()) == []


async def test_a_second_failure_is_an_error(runs: Runs, tmp_path: Path) -> None:
    runs.outcomes = [(1, COLOUR), (1, "BadArgsError: still no\n")]
    with pytest.raises(RuntimeError, match="exit code 1"):
        await convert(runs, tmp_path)
    assert len(runs.calls) == 2

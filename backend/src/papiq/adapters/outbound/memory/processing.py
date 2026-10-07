"""Stand-ins for OCR, parser and preview renderer: fast, deterministic, no external programs.

They follow the contracts without real recognition: the archive is a minimal PDF that names the
source, the Markdown is a short text, the preview a tiny WebP header. A file that is neither a
PDF ending with `%%EOF` nor a supported image counts as damaged.
"""

import asyncio
import json
from pathlib import Path

from papiq.core.domain import media_types
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports.ocr import OcrResult
from papiq.core.ports.parser import ParseResult

FAKE_PDF = b"%PDF-1.7\n% papiq fake archive\n%%EOF\n"
FAKE_WEBP = b"RIFF\x0c\x00\x00\x00WEBPVP8 "


class FakeOcr:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        self.calls.append(source)
        data = await asyncio.to_thread(source.read_bytes)
        _check(data)
        await asyncio.to_thread(target.write_bytes, FAKE_PDF)
        return OcrResult(pages=1, engine="fake-ocr 1", pdfa=True)


class FakeParser:
    def __init__(self, text: str = "Fake text") -> None:
        self.text = text
        self.calls: list[Path] = []

    async def parse(self, source: Path, *, markdown: Path, structure: Path) -> ParseResult:
        self.calls.append(source)
        _check(await asyncio.to_thread(source.read_bytes))
        await asyncio.to_thread(markdown.write_text, self.text, encoding="utf-8")
        await asyncio.to_thread(
            structure.write_text, json.dumps({"text": self.text}), encoding="utf-8"
        )
        return ParseResult(pages=1, parser="fake-parser 1")


class FakePreviewRenderer:
    async def render_first_page(self, source: Path, target: Path) -> None:
        _check(await asyncio.to_thread(source.read_bytes))
        await asyncio.to_thread(target.write_bytes, FAKE_WEBP)


def _check(data: bytes) -> None:
    kind = media_types.detect(data[: media_types.SNIFF_SIZE])
    if kind is None or (kind == media_types.PDF and not data.rstrip().endswith(b"%%EOF")):
        raise UnprocessableDocumentError("the file is damaged or not a valid document")

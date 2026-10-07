"""Contracts of the processing ports: OCR, parser, preview renderer. The samples are in
`tests/samples` (see `make_samples.py`)."""

import json
from pathlib import Path

import pytest

from papiq.core.domain import media_types
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.ports import DocumentParser, Ocr, PreviewRenderer

SAMPLES = Path(__file__).parent.parent / "samples"


class OcrContract:
    """Needs the fixture `ocr`."""

    @pytest.mark.parametrize(
        ("sample", "media_type"),
        [
            ("scan.pdf", media_types.PDF),
            ("text.pdf", media_types.PDF),
            ("photo.jpg", media_types.JPEG),
            ("screenshot.png", media_types.PNG),
        ],
    )
    async def test_makes_an_archive_pdf(
        self, ocr: Ocr, tmp_path: Path, sample: str, media_type: str
    ) -> None:
        target = tmp_path / "archive.pdf"
        result = await ocr.make_archive(SAMPLES / sample, target, media_type=media_type)
        assert target.read_bytes().startswith(b"%PDF-")
        assert result.pages == 1
        assert result.engine
        assert sorted(path.name for path in tmp_path.iterdir()) == ["archive.pdf"]

    async def test_replaces_the_target(self, ocr: Ocr, tmp_path: Path) -> None:
        target = tmp_path / "archive.pdf"
        target.write_bytes(b"old")
        await ocr.make_archive(SAMPLES / "scan.pdf", target, media_type=media_types.PDF)
        assert target.read_bytes().startswith(b"%PDF-")

    async def test_a_damaged_file_is_unprocessable(self, ocr: Ocr, tmp_path: Path) -> None:
        target = tmp_path / "archive.pdf"
        with pytest.raises(UnprocessableDocumentError):
            await ocr.make_archive(SAMPLES / "damaged.pdf", target, media_type=media_types.PDF)
        assert list(tmp_path.iterdir()) == []


class ParserContract:
    """Needs the fixture `parser`."""

    async def test_writes_markdown_and_structure(
        self, parser: DocumentParser, tmp_path: Path
    ) -> None:
        markdown, structure = tmp_path / "content.md", tmp_path / "content.json"
        result = await parser.parse(SAMPLES / "text.pdf", markdown=markdown, structure=structure)
        assert markdown.read_text(encoding="utf-8").strip()
        assert isinstance(json.loads(structure.read_text(encoding="utf-8")), dict)
        assert result.pages == 1
        assert result.parser
        assert sorted(path.name for path in tmp_path.iterdir()) == ["content.json", "content.md"]

    async def test_a_damaged_file_is_unprocessable(
        self, parser: DocumentParser, tmp_path: Path
    ) -> None:
        with pytest.raises(UnprocessableDocumentError):
            await parser.parse(
                SAMPLES / "damaged.pdf",
                markdown=tmp_path / "content.md",
                structure=tmp_path / "content.json",
            )
        assert list(tmp_path.iterdir()) == []


class PreviewRendererContract:
    """Needs the fixture `preview_renderer`."""

    async def test_renders_a_webp_image(
        self, preview_renderer: PreviewRenderer, tmp_path: Path
    ) -> None:
        target = tmp_path / "preview.webp"
        await preview_renderer.render_first_page(SAMPLES / "scan.pdf", target)
        data = target.read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert [path.name for path in tmp_path.iterdir()] == ["preview.webp"]

    async def test_a_damaged_pdf_is_unprocessable(
        self, preview_renderer: PreviewRenderer, tmp_path: Path
    ) -> None:
        with pytest.raises(UnprocessableDocumentError):
            await preview_renderer.render_first_page(
                SAMPLES / "damaged.pdf", tmp_path / "preview.webp"
            )
        assert list(tmp_path.iterdir()) == []

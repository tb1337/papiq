"""Docling with local models: passes the contract and finds the text. Slow (models load per
document): marker `docling`. Skips if the models are missing (`PAPIQ_DOCLING_MODELS_PATH`;
the devcontainer image has them)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from papiq.adapters.outbound.docling import DoclingParser
from papiq.composition.settings import Settings
from tests.contracts.processing import SAMPLES, ParserContract

pytestmark = pytest.mark.docling


@pytest.fixture(scope="session")
def models(settings: Settings) -> Path:
    path = settings.docling_models_path
    if not path.is_dir():
        pytest.skip(f"Docling models not found in {path}")
    return path


@pytest.fixture
def parser(models: Path) -> DoclingParser:
    return DoclingParser(models=models, timeout=timedelta(minutes=5))


class TestDoclingParser(ParserContract):
    pass


async def test_text_and_structure(parser: DoclingParser, tmp_path: Path) -> None:
    markdown, structure = tmp_path / "content.md", tmp_path / "content.json"
    result = await parser.parse(SAMPLES / "text.pdf", markdown=markdown, structure=structure)
    text = " ".join(markdown.read_text(encoding="utf-8").split())
    assert "Rechnung Nummer 4711" in text
    assert json.loads(structure.read_text(encoding="utf-8"))["schema_name"] == "DoclingDocument"
    assert result.parser.startswith("docling ")


async def test_missing_models_are_reported(tmp_path: Path) -> None:
    parser = DoclingParser(models=tmp_path / "missing", timeout=timedelta(minutes=1))
    with pytest.raises(RuntimeError, match="models not found"):
        await parser.parse(
            SAMPLES / "text.pdf",
            markdown=tmp_path / "content.md",
            structure=tmp_path / "content.json",
        )


async def test_the_time_limit_stops_parsing(models: Path, tmp_path: Path) -> None:
    parser = DoclingParser(models=models, timeout=timedelta(milliseconds=200))
    with pytest.raises(TimeoutError, match="did not finish"):
        await parser.parse(
            SAMPLES / "text.pdf",
            markdown=tmp_path / "content.md",
            structure=tmp_path / "content.json",
        )
    assert list(tmp_path.iterdir()) == []

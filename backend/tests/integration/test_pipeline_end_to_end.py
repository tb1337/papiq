"""A scan and a photo run through OCR (OCRmyPDF) and parsing (Docling) on the filesystem and
on S3 (Garage); archive PDF, preview and Docling output end up in the object store.
Metadata stays in memory. Marker `docling` (slow)."""

import shutil
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from papiq.adapters.outbound.docling import DoclingParser
from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.pdfium import PdfiumPreviewRenderer
from papiq.composition.container import build_memory_container, build_services
from papiq.composition.settings import Settings
from papiq.core.domain import media_types
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.ports import ObjectStore
from papiq.core.services.objects import archive_key, markdown_key, preview_key, structure_key
from tests import builders
from tests.builders import incoming
from tests.contracts.processing import SAMPLES
from tests.integration.conftest import s3_test_store

pytestmark = pytest.mark.docling

if not (shutil.which("tesseract") and shutil.which("gs")):
    pytest.skip("Tesseract or Ghostscript not installed", allow_module_level=True)


@pytest.fixture(params=["filesystem", "s3"])
async def object_store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[ObjectStore]:
    if request.param == "filesystem":
        yield FilesystemObjectStore(tmp_path / "objects")
        return
    settings: Settings = request.getfixturevalue("s3_settings")
    async with s3_test_store(settings) as store:
        yield store


@pytest.mark.parametrize(
    ("sample", "media_type"), [("scan.pdf", media_types.PDF), ("photo.jpg", media_types.JPEG)]
)
async def test_scan_and_photo_are_processed(
    object_store: ObjectStore, settings: Settings, sample: str, media_type: str
) -> None:
    if not settings.docling_models_path.is_dir():
        pytest.skip(f"Docling models not found in {settings.docling_models_path}")
    container = replace(
        build_memory_container(),
        object_store=object_store,
        ocr=OcrmypdfEngine(languages=["deu", "eng"], timeout=timedelta(minutes=5)),
        parser=DoclingParser(models=settings.docling_models_path, timeout=timedelta(minutes=5)),
        previews=PdfiumPreviewRenderer(),
    )
    services = build_services(container)
    owner = builders.user()
    async with container.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.drawers.add(builders.default_drawer(owner))
        await uow.commit()

    document = await services.pipeline.receive(
        owner.id, incoming((SAMPLES / sample).read_bytes()), filename=sample
    )
    while await services.pipeline.run_next_job():
        pass

    stored = await services.documents.get(owner.id, document.id)
    log = await services.documents.processing_log(owner.id, document.id)
    assert stored.lane is Lane.GREEN, [entry.result for entry in log]
    assert (await object_store.get(archive_key(document.id))).startswith(b"%PDF-")
    assert (await object_store.get(preview_key(document.id)))[8:12] == b"WEBP"
    markdown = (await object_store.get(markdown_key(document.id))).decode()
    assert "Rechnung Nummer 4711" in " ".join(markdown.split())
    assert await object_store.exists(structure_key(document.id))
    models = {entry.step: entry.result.model_version or "" for entry in log}
    assert models[Step.OCR].startswith("ocrmypdf")
    assert models[Step.PARSE].startswith("docling")

"""OCR and parse steps on the fakes: derivatives, outcomes, failures."""

import tempfile
from pathlib import Path

import pytest

from papiq.adapters.outbound.memory import FakeOcr, FakeParser, FakePreviewRenderer
from papiq.adapters.outbound.memory.processing import FAKE_PDF
from papiq.core.domain.documents import Document
from papiq.core.domain.pipeline import Lane, Outcome, Step
from papiq.core.domain.users import User
from papiq.core.ports import OcrResult
from papiq.core.services.objects import (
    archive_key,
    markdown_key,
    original_key,
    preview_key,
    structure_key,
)
from papiq.core.services.pipeline import PipelineService
from papiq.core.services.steps import OcrStep, ParseStep, has_text
from tests.builders import incoming
from tests.contracts.processing import SAMPLES
from tests.unit.services.conftest import World


class NotPdfa(FakeOcr):
    def __init__(self, note: str | None = None) -> None:
        super().__init__()
        self.note = note

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        await super().make_archive(source, target, media_type=media_type)
        return OcrResult(pages=1, engine="fake-ocr 1", pdfa=False, note=self.note)


class Unsigned(FakeOcr):
    """PDF/A, but the original's signature is not in the archive."""

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        await super().make_archive(source, target, media_type=media_type)
        return OcrResult(pages=1, engine="fake-ocr 1", pdfa=True, note="signature not carried")


def pipeline(
    world: World, *, ocr: FakeOcr | None = None, text: str = "Rechnung"
) -> PipelineService:
    store = world.object_store
    return world.pipeline(
        {
            Step.OCR: OcrStep(store, ocr or FakeOcr(), FakePreviewRenderer()),
            Step.PARSE: ParseStep(store, FakeParser(text)),
        }
    )


async def ingest(world: World, service: PipelineService, sample: str) -> tuple[User, Document]:
    owner = await world.user()
    data = (SAMPLES / sample).read_bytes()
    document = await service.receive(owner.id, incoming(data), filename=sample)
    await world.drain(service)
    return owner, await world.documents.get(owner.id, document.id)


@pytest.mark.parametrize("sample", ["scan.pdf", "photo.jpg"])
async def test_ocr_and_parse_store_their_derivatives(world: World, sample: str) -> None:
    owner, document = await ingest(world, pipeline(world), sample)
    assert document.lane is Lane.GREEN
    store = world.object_store
    assert await store.get(archive_key(document.id)) == FAKE_PDF
    assert store.content_type(archive_key(document.id)) == "application/pdf"
    assert store.content_type(preview_key(document.id)) == "image/webp"
    assert await store.get(markdown_key(document.id)) == b"Rechnung"
    assert store.content_type(structure_key(document.id)) == "application/json"

    log = {
        entry.step: entry for entry in await world.documents.processing_log(owner.id, document.id)
    }
    ocr, parse = log[Step.OCR].result, log[Step.PARSE].result
    assert ocr.input == {"original": original_key(document.sha256)}
    assert ocr.output == {
        "archive": archive_key(document.id),
        "preview": preview_key(document.id),
        "pages": 1,
        "pdfa": True,
    }
    assert ocr.model_version == "fake-ocr 1"
    assert parse.input == {"archive": archive_key(document.id)}
    assert parse.output["characters"] == len("Rechnung")
    assert parse.model_version == "fake-parser 1"


async def test_a_damaged_file_goes_red_at_once(world: World) -> None:
    ocr = FakeOcr()
    owner, document = await ingest(world, pipeline(world, ocr=ocr), "damaged.pdf")
    assert document.lane is Lane.RED
    assert document.processing.current_step is Step.OCR
    assert len(ocr.calls) == 1
    entry = (await world.documents.processing_log(owner.id, document.id))[-1]
    assert entry.result.reason == "the file is damaged or not a valid document"
    assert not await world.object_store.exists(archive_key(document.id))


async def test_an_archive_that_is_not_pdfa_is_uncertain(world: World) -> None:
    owner, document = await ingest(world, pipeline(world, ocr=NotPdfa()), "scan.pdf")
    assert document.lane is Lane.YELLOW
    assert document.processing.outcomes[Step.OCR] is Outcome.UNCERTAIN
    entry = (await world.documents.processing_log(owner.id, document.id))[1]
    assert entry.step is Step.OCR
    assert entry.result.reason == "the archive could not be made PDF/A"
    assert "note" not in entry.result.output


async def test_the_engines_reason_for_a_plain_pdf_is_in_the_log(world: World) -> None:
    note = "not PDF/A: Setting Overprint Mode to 1 not permitted in PDF/A-2"
    owner, document = await ingest(world, pipeline(world, ocr=NotPdfa(note)), "scan.pdf")
    assert document.lane is Lane.YELLOW
    entry = (await world.documents.processing_log(owner.id, document.id))[1]
    assert entry.result.reason == f"the archive could not be made PDF/A: {note}"
    assert entry.result.output["note"] == note


async def test_a_signature_left_out_of_the_archive_is_ok_with_a_note(world: World) -> None:
    owner, document = await ingest(world, pipeline(world, ocr=Unsigned()), "scan.pdf")
    assert document.lane is Lane.GREEN
    entry = (await world.documents.processing_log(owner.id, document.id))[1]
    assert entry.step is Step.OCR and entry.result.outcome is Outcome.OK
    assert entry.result.reason == "signature not carried"
    assert entry.result.output["note"] == "signature not carried"


async def test_a_document_without_text_goes_red(world: World) -> None:
    owner, document = await ingest(world, pipeline(world, text="<!-- image -->\n\n"), "blank.pdf")
    assert document.lane is Lane.RED
    assert document.processing.current_step is Step.PARSE
    entry = (await world.documents.processing_log(owner.id, document.id))[-1]
    assert entry.result.reason == "no text recognised"
    assert await world.object_store.exists(markdown_key(document.id))  # kept for inspection


async def test_reprocessing_from_parse_reuses_the_archive(world: World) -> None:
    ocr = FakeOcr()
    service = pipeline(world, ocr=ocr)
    owner, document = await ingest(world, service, "scan.pdf")
    await service.reprocess_from(owner.id, document.id, Step.PARSE)
    await world.drain(service)
    assert len(ocr.calls) == 1
    assert (await world.documents.get(owner.id, document.id)).lane is Lane.GREEN


async def test_work_directories_are_removed(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    await ingest(world, pipeline(world), "scan.pdf")
    await ingest(world, pipeline(world), "damaged.pdf")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [("", False), ("  \n", False), ("<!-- image -->", False), ("# 4711", True), ("Ä", True)],
)
def test_has_text(markdown: str, expected: bool) -> None:
    assert has_text(markdown) is expected

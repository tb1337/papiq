"""The technical pipeline steps after receive: OCR and parsing.

Each step downloads its input into a temporary directory, runs the port (which does the heavy
work outside the event loop) and uploads the derivatives. Derivatives are keyed by document, so
reprocessing replaces them.
"""

import asyncio
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from papiq.core.domain import media_types
from papiq.core.domain.documents import Document
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.pipeline import Outcome, StepResult
from papiq.core.ports import DocumentParser, ObjectStore, Ocr, PreviewRenderer
from papiq.core.ports.preview import PREVIEW_MEDIA_TYPE
from papiq.core.services.objects import (
    archive_key,
    markdown_key,
    original_key,
    preview_key,
    structure_key,
)

# Markdown without any letter or digit has no text; comments are placeholders (`<!-- image -->`).
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WORD = re.compile(r"\w")


class OcrStep:
    """Archive PDF with text layer and preview of the first page. A damaged or encrypted file
    fails at once (UnprocessableDocumentError, see PipelineService); an archive that is not
    PDF/A is uncertain."""

    def __init__(self, store: ObjectStore, ocr: Ocr, previews: PreviewRenderer) -> None:
        self._store = store
        self._ocr = ocr
        self._previews = previews

    async def run(self, document: Document) -> StepResult:
        async with work_directory() as work:
            original, archive, preview = work / "original", work / "archive.pdf", work / "preview"
            await self._store.download(original_key(document.sha256), original)
            result = await self._ocr.make_archive(original, archive, media_type=document.media_type)
            await self._previews.render_first_page(archive, preview)
            await self._store.upload(
                archive_key(document.id), archive, content_type=media_types.PDF
            )
            await self._store.upload(
                preview_key(document.id), preview, content_type=PREVIEW_MEDIA_TYPE
            )
        input: JsonObject = {"original": original_key(document.sha256)}
        output: JsonObject = {
            "archive": archive_key(document.id),
            "preview": preview_key(document.id),
            "pages": result.pages,
            "pdfa": result.pdfa,
        }
        if not result.pdfa:
            return StepResult(
                outcome=Outcome.UNCERTAIN,
                reason="the archive could not be made PDF/A",
                model_version=result.engine,
                input=input,
                output=output,
            )
        return StepResult(
            outcome=Outcome.OK, model_version=result.engine, input=input, output=output
        )


class ParseStep:
    """Markdown and structure from the archive PDF. A document without any text fails: there
    is nothing to classify, a person has to look at it."""

    def __init__(self, store: ObjectStore, parser: DocumentParser) -> None:
        self._store = store
        self._parser = parser

    async def run(self, document: Document) -> StepResult:
        async with work_directory() as work:
            archive, markdown, structure = (
                work / "archive.pdf",
                work / "content.md",
                work / "content.json",
            )
            await self._store.download(archive_key(document.id), archive)
            result = await self._parser.parse(archive, markdown=markdown, structure=structure)
            text = await asyncio.to_thread(markdown.read_text, encoding="utf-8")
            await self._store.upload(
                markdown_key(document.id), markdown, content_type="text/markdown; charset=utf-8"
            )
            await self._store.upload(
                structure_key(document.id), structure, content_type="application/json"
            )
        input: JsonObject = {"archive": archive_key(document.id)}
        output: JsonObject = {
            "markdown": markdown_key(document.id),
            "structure": structure_key(document.id),
            "pages": result.pages,
            "characters": len(text),
        }
        if not has_text(text):
            return StepResult(
                outcome=Outcome.FAILED,
                reason="no text recognised",
                model_version=result.parser,
                input=input,
                output=output,
            )
        return StepResult(
            outcome=Outcome.OK, model_version=result.parser, input=input, output=output
        )


def has_text(markdown: str) -> bool:
    return _WORD.search(_COMMENT.sub("", markdown)) is not None


@asynccontextmanager
async def work_directory() -> AsyncIterator[Path]:
    """A temporary directory, removed afterwards (also on error or cancellation)."""
    path = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="papiq-"))
    try:
        yield path
    finally:
        await asyncio.shield(asyncio.to_thread(shutil.rmtree, path, ignore_errors=True))

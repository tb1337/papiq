"""Classification and attribute extraction for documents taken over from another system.

A document received with metadata (channel `migration`, see `PipelineService.receive`) keeps
what the source system knew: these steps apply it instead of asking the language model. Every
other document goes to the step they wrap. The applied values are not model output, so rules
trust them (they have no field checks in the log, see `rules.running.provenance`).
"""

from papiq.core.domain.documents import Channel, Document, DocumentChanges
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.imports import IMPORTED, ImportedMetadata
from papiq.core.domain.pipeline import Outcome, Step, StepResult
from papiq.core.ports import UnitOfWorkFactory
from papiq.core.services.pipeline import DeferredResult, MetadataResult, StepExecutor

REASON = "taken over from the source system"


class _ImportedStep:
    def __init__(self, uow: UnitOfWorkFactory, inner: StepExecutor) -> None:
        self._uow = uow
        self._inner = inner

    async def run(self, document: Document) -> StepResult | DeferredResult:
        if document.channel is not Channel.MIGRATION:
            return await self._inner.run(document)
        async with self._uow() as uow:
            entries = await uow.processing_log.list_for(document.id)
            definitions = {item.id: item for item in await uow.attributes.list_all()}
        stored = next(
            (
                entry.result.output[IMPORTED]
                for entry in entries
                if entry.step is Step.RECEIVE and IMPORTED in entry.result.output
            ),
            None,
        )
        if stored is None:
            return await self._inner.run(document)
        try:
            return self._apply(ImportedMetadata.from_json(stored, definitions))
        except ValidationError as error:
            return StepResult(
                outcome=Outcome.UNCERTAIN,
                reason=f"the metadata of the source system no longer fits: {error}",
            )

    def _apply(self, imported: ImportedMetadata) -> StepResult | DeferredResult:
        raise NotImplementedError


class ImportedClassifyStep(_ImportedStep):
    """Title, contact, document type, tags and document date from the source system."""

    def _apply(self, imported: ImportedMetadata) -> MetadataResult:
        result = StepResult(
            outcome=Outcome.OK,
            reason=REASON,
            model_version=IMPORTED,
            output={IMPORTED: imported.to_json()},
        )
        return MetadataResult(result, imported.classification(), imported.tag_ids)


class ImportedExtractStep(_ImportedStep):
    """Attribute values from the source system."""

    def _apply(self, imported: ImportedMetadata) -> MetadataResult:
        result = StepResult(
            outcome=Outcome.OK,
            reason=REASON,
            model_version=IMPORTED,
            output={"attributes": len(imported.attributes)},
        )
        return MetadataResult(result, DocumentChanges(attributes=dict(imported.attributes)))

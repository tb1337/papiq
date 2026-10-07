"""Running the evaluation set through the pipeline's classification and extraction steps.

Each case becomes a document of one owner, whose text is stored as if the parse step had
produced it. OCR, parsing, rules and filing pass through; classification and attribute
extraction are the same steps as in the pipeline, with the given model.
"""

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from papiq.adapters.inbound.evaluation.cases import Case, EvaluationSet, MasterDataSpec
from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.classification import FieldCheck, attribute_field, attribute_to_json
from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.ids import AttributeId, ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.pipeline import PIPELINE, Lane, ProcessingStatus, Step, StepRun
from papiq.core.domain.users import Role, User
from papiq.core.ports import Clock, LanguageModel, ObjectStore, UnitOfWorkFactory
from papiq.core.services.classification.steps import (
    ClassificationPolicy,
    ClassifyStep,
    ExtractAttributesStep,
)
from papiq.core.services.inbox import field_checks, open_steps
from papiq.core.services.objects import markdown_key
from papiq.core.services.pipeline import (
    IncomingFile,
    PipelineService,
    PlaceholderStep,
    RetryPolicy,
    StepExecutor,
)

PIPELINE_VERSION = "evaluation"
_MAX_JOBS = 50  # per case; the pipeline needs one per step


@dataclass(frozen=True)
class Environment:
    """Where the cases run: a database and an object store of their own (in memory)."""

    uow: UnitOfWorkFactory
    store: ObjectStore
    clock: Clock


@dataclass(frozen=True)
class MasterData:
    """The seeded master data, by id."""

    contacts: dict[ContactId, str]
    document_types: dict[DocumentTypeId, str]
    tags: dict[TagId, str]
    attributes: dict[AttributeId, AttributeDefinition]


@dataclass(frozen=True)
class CaseResult:
    """What became of a case: the document's state, the field checks and the run time."""

    case: Case
    lane: Lane | None
    status: ProcessingStatus
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    attributes: dict[str, JsonValue]
    checks: dict[str, FieldCheck]  # by field; attribute fields by attribute name
    proposed: dict[str, JsonValue]  # by field as `checks`: the value, else the suggestion, by name
    reasons: dict[Step, str]  # steps that are uncertain or failed
    seconds: float
    violations: tuple[str, ...]  # changes no check backs, or of fields the model must not touch
    truncated: bool


async def evaluate(
    evaluation: EvaluationSet,
    environment: Environment,
    model_for: Callable[[Case], LanguageModel | None],
    policy: ClassificationPolicy,
    *,
    progress: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Run every case; the cases share the master data."""
    owner = await _owner(environment)
    master = await _seed(environment, evaluation.master_data)
    results = []
    for case in evaluation.cases:
        result = await _run(case, environment, master, owner, model_for(case), policy)
        if progress is not None:
            progress(result)
        results.append(result)
    return results


async def _owner(environment: Environment) -> User:
    now = environment.clock.now()
    owner = User.create(username="evaluation", role=Role.USER, now=now)
    async with environment.uow() as uow:
        await uow.users.add(owner)
        await uow.drawers.add(Drawer.create_default(owner_id=owner.id, now=now))
        await uow.commit()
    return owner


async def _seed(environment: Environment, spec: MasterDataSpec) -> MasterData:
    now = environment.clock.now()
    contacts = [Contact.create(name=name, now=now) for name in spec.contacts]
    types = [DocumentType.create(name=name, now=now) for name in spec.document_types]
    tags = [Tag.create(name=name, now=now) for name in spec.tags]
    type_ids = {item.name: item.id for item in types}
    attributes = [
        AttributeDefinition.create(
            name=item.name,
            data_type=item.data_type,
            now=now,
            document_type_ids=(
                None
                if item.document_types is None
                else [type_ids[name] for name in item.document_types]
            ),
            choices=item.choices,
        )
        for item in spec.attributes
    ]
    async with environment.uow() as uow:
        for contact in contacts:
            await uow.contacts.add(contact)
        for document_type in types:
            await uow.document_types.add(document_type)
        for tag in tags:
            await uow.tags.add(tag)
        for attribute in attributes:
            await uow.attributes.add(attribute)
        await uow.commit()
    return MasterData(
        contacts={item.id: item.name for item in contacts},
        document_types={item.id: item.name for item in types},
        tags={item.id: item.name for item in tags},
        attributes={item.id: item for item in attributes},
    )


async def _run(
    case: Case,
    environment: Environment,
    master: MasterData,
    owner: User,
    model: LanguageModel | None,
    policy: ClassificationPolicy,
) -> CaseResult:
    uow, store, clock = environment.uow, environment.store, environment.clock
    executors: dict[Step, StepExecutor] = {step: PlaceholderStep() for step in PIPELINE[1:]}
    executors[Step.CLASSIFY] = ClassifyStep(uow, store, model, clock, policy)
    executors[Step.EXTRACT_ATTRIBUTES] = ExtractAttributesStep(uow, store, model, clock, policy)
    # No automatic retries: a model that cannot be reached ends the case red at once.
    pipeline = PipelineService(
        uow,
        clock,
        store,
        executors,
        pipeline_version=PIPELINE_VERSION,
        retry=RetryPolicy(max_attempts=1),
    )
    with tempfile.TemporaryDirectory(prefix="papiq-evaluation-") as directory:
        path = Path(directory) / "case.pdf"
        # A distinct file per case: the owner cannot have the same file twice.
        path.write_bytes(f"%PDF-1.7\n% evaluation case {case.name}\n%%EOF\n".encode())
        received = await pipeline.receive(
            owner.id, IncomingFile.of(path), filename=f"{case.name}.pdf"
        )
    await store.put(
        markdown_key(received.id),
        case.text.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    started = time.perf_counter()
    for _ in range(_MAX_JOBS):
        if not await pipeline.run_next_job():
            break
    seconds = time.perf_counter() - started
    async with uow() as session:
        document = await session.documents.get(received.id)
        log = await session.processing_log.list_for(received.id)
    return _result(case, received, document, log, master, seconds)


def _result(
    case: Case,
    before: Document,
    after: Document,
    log: list[StepRun],
    master: MasterData,
    seconds: float,
) -> CaseResult:
    checks = {_field_label(field, master): check for field, check in field_checks(log).items()}
    truncated = any(
        entry.result.input.get("truncated") is True
        for entry in log
        if entry.step in (Step.CLASSIFY, Step.EXTRACT_ATTRIBUTES)
    )
    return CaseResult(
        case=case,
        lane=after.lane,
        status=after.processing.status,
        contact=None if after.contact_id is None else master.contacts[after.contact_id],
        document_type=(
            None
            if after.document_type_id is None
            else master.document_types[after.document_type_id]
        ),
        tags=frozenset(master.tags[tag] for tag in after.tag_ids),
        document_date=None if after.document_date is None else after.document_date.isoformat(),
        attributes={
            master.attributes[id].name: attribute_to_json(value)
            for id, value in after.attributes.items()
        },
        checks=checks,
        proposed={label: _resolved(check, master) for label, check in checks.items()},
        reasons={item.step: item.reason or "" for item in open_steps(after, log)},
        seconds=seconds,
        violations=_violations(before, after, field_checks(log)),
        truncated=truncated,
    )


def _resolved(check: FieldCheck, master: MasterData) -> JsonValue:
    """The checked value, or else the suggestion, with contact, type and tag ids as names."""
    value = check.value if check.ok else check.suggestion
    names: dict[str, str]
    match check.field:
        case "contact":
            names = {str(id): name for id, name in master.contacts.items()}
        case "document_type":
            names = {str(id): name for id, name in master.document_types.items()}
        case "tags":
            names = {str(id): name for id, name in master.tags.items()}
            tags: list[JsonValue] = []
            tags.extend(sorted(names.get(item, item) for item in _strings(value)))
            return tags
        case _:
            return value
    return names.get(value, value) if isinstance(value, str) else value


def _field_label(field: str, master: MasterData) -> str:
    for id, definition in master.attributes.items():
        if field == attribute_field(id):
            return definition.name
    return field


def _violations(
    before: Document, after: Document, checks: dict[str, FieldCheck]
) -> tuple[str, ...]:
    """Changes the checks do not back: every changed field must have an OK check with the
    applied value; drawer, owner and title must stay."""
    problems = []
    for name in ("owner_id", "drawer_id", "title"):
        if getattr(before, name) != getattr(after, name):
            problems.append(f"{name} changed")

    def backed(field: str, value: JsonValue) -> bool:
        check = checks.get(field)
        return check is not None and check.ok and check.value == value

    if after.contact_id != before.contact_id and not backed(
        "contact", None if after.contact_id is None else str(after.contact_id)
    ):
        problems.append("contact changed without a passed check")
    if after.document_type_id != before.document_type_id and not backed(
        "document_type", None if after.document_type_id is None else str(after.document_type_id)
    ):
        problems.append("document type changed without a passed check")
    if after.tag_ids != before.tag_ids:
        check = checks.get("tags")
        if (
            check is None
            or not check.ok
            or set(_strings(check.value)) != {str(tag) for tag in after.tag_ids}
        ):
            problems.append("tags changed without a passed check")
    if after.document_date != before.document_date and not backed(
        "document_date", None if after.document_date is None else after.document_date.isoformat()
    ):
        problems.append("document date changed without a passed check")
    for id, value in after.attributes.items():
        if before.attributes.get(id) != value and not backed(
            attribute_field(id), attribute_to_json(value)
        ):
            problems.append(f"attribute {id} changed without a passed check")
    return tuple(problems)


def _strings(value: JsonValue) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

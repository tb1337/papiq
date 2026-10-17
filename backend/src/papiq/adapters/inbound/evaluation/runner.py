"""Running the evaluation set through the pipeline's signals, judge and fields node.

Each case becomes a document of one owner, whose text is stored as if the parse node had
produced it. OCR, parsing, rules and filing pass through; the signals, the judge and the
fields node are the same as in the pipeline, with the given model.

A run is cold (a fresh installation: the classifier and the similar documents abstain) or
trained: the training corpus is taken over first as from Paperless, indexed, and the
classifier trained on it. After each case a person sets the expected values (confirming in
the inbox, or changing a green document), and the log tells which automatic values they
corrected.
"""

import secrets
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from papiq.adapters.inbound.evaluation.cases import (
    Case,
    EvaluationSet,
    Expected,
    MasterDataSpec,
    Run,
    TrainingDocument,
)
from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_DATE,
    DOCUMENT_TYPE,
    TAGS,
    TITLE,
    TOPIC,
    FieldCheck,
    field_key,
)
from papiq.core.domain.documents import UNSET, Channel, Document, DocumentChanges
from papiq.core.domain.fields import FieldDefinition, field_from_json, field_to_json
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.ids import ContactId, DocumentId, DocumentTypeId, FieldId, TagId, UserId
from papiq.core.domain.imports import ImportedMetadata
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.master_data import TagScope
from papiq.core.domain.pipeline import Lane, Node, NodeRun, ProcessingStatus
from papiq.core.domain.users import Role, User
from papiq.core.ports import ObjectStore
from papiq.core.services.classifier import ClassifierService
from papiq.core.services.documents import DocumentService
from papiq.core.services.inbox import field_checks, open_nodes
from papiq.core.services.indexing import IndexingService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.objects import text_key
from papiq.core.services.pipeline import IncomingFile, PipelineService
from papiq.core.services.users import UserService

ADMIN = "evaluation-admin"  # creates the master data
OWNER = "evaluation"  # owns the documents
_MAX_JOBS = 50  # per case; the pipeline needs one per node


@dataclass(frozen=True)
class Environment:
    """Where the cases run: the services on a database and an object store of their own (in
    memory, or the real ones in the integration test), built by the composition root.

    `pipeline_for(case)` is the pipeline the case runs through: the signals, the judge and the
    fields node with the case's model, the other nodes pass through, and no automatic
    retries, so a model that cannot be reached ends the case red at once; `pipeline_for(None)`
    is the one without a model the training corpus is taken over through. A trained run has
    the services to index the corpus and train the classifier.
    """

    store: ObjectStore  # the text of a case is stored as the parse node would have
    users: UserService
    master_data: MasterDataService
    documents: DocumentService
    pipeline_for: Callable[[Case | None], PipelineService]
    run: Run = Run.COLD
    indexing: IndexingService | None = None
    classifier: ClassifierService | None = None


@dataclass(frozen=True)
class MasterData:
    """The seeded master data, by id."""

    contacts: dict[ContactId, str]
    document_types: dict[DocumentTypeId, str]
    tags: dict[TagId, str]
    fields: dict[FieldId, FieldDefinition]
    topics: dict[TagId, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseResult:
    """What became of a case: the document's state, the field checks and the run time."""

    case: Case
    document_id: DocumentId
    owner_id: UserId
    lane: Lane | None
    status: ProcessingStatus
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    fields: dict[str, JsonValue]
    checks: dict[str, FieldCheck]  # by field; fields by their name
    proposed: dict[str, JsonValue]  # by field as `checks`: the value, else the suggestion, by name
    reasons: dict[Node, str]  # nodes that are uncertain or failed
    seconds: float
    violations: tuple[str, ...]  # changes no check backs, or of fields the model must not touch
    truncated: bool
    run: Run = Run.COLD
    automatic: frozenset[str] = frozenset()  # fields set without a person, by name as `checks`
    corrected: frozenset[str] = frozenset()  # fields the person changed afterwards, likewise
    by_hand: frozenset[str] = frozenset()  # fields set by hand only: no model checks, not scored
    topic: str | None = None


async def evaluate(
    evaluation: EvaluationSet,
    environment: Environment,
    *,
    filename_for: Callable[[Case], str] | None = None,
    progress: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Run every case of the environment's run; the cases share the owner and the master data.
    `filename_for`: the file name a case is received with (default: the case's name)."""
    admin, owner = await _accounts(environment)
    master = await _seed(environment, evaluation.master_data, admin)
    if environment.run is Run.TRAINED:
        await _take_over(environment, evaluation.training, master, admin, owner)
    results = []
    for case in evaluation.cases_in(environment.run):
        filename = f"{case.name}.pdf" if filename_for is None else filename_for(case)
        result = await _run(case, environment, master, owner, filename)
        result = await _corrected(result, environment, master, owner)
        if progress is not None:
            progress(result)
        results.append(result)
    return results


async def _accounts(environment: Environment) -> tuple[User, User]:
    """An admin, who creates the master data, and a user, who owns the documents. The database
    has to be empty: the admin can only be the first one."""
    admin = await environment.users.bootstrap_admin(ADMIN, secrets.token_urlsafe(24))
    if admin is None:
        raise RuntimeError("the evaluation needs a database without an admin")
    owner = await environment.users.create_user(admin.id, OWNER, Role.USER)
    return admin, owner


async def _seed(environment: Environment, spec: MasterDataSpec, admin: User) -> MasterData:
    master_data = environment.master_data
    topics: dict[str, TagId] = {}
    for name, parent in spec.topics:
        topic = await master_data.create_tag(
            admin.id,
            name,
            scope=TagScope.TOPIC,
            parent_id=None if parent is None else topics[parent],
        )
        topics[name] = topic.id
    contacts = [
        await master_data.create_contact(
            admin.id,
            name,
            spec.contact_aliases.get(name, ()),
            [
                Identifier.parse(
                    IdentifierKind(item.kind),
                    item.value,
                    topic_id=None if item.topic is None else topics[item.topic],
                )
                for item in spec.contact_identifiers.get(name, ())
            ],
            topic_ids=[topics[topic] for topic in spec.contact_topics.get(name, ())],
        )
        for name in spec.contacts
    ]
    types = [
        await master_data.create_document_type(
            admin.id,
            name,
            spec.type_descriptions.get(name),
            spec.title_patterns.get(name),
        )
        for name in spec.document_types
    ]
    tags = [await master_data.create_tag(admin.id, name) for name in spec.tags]
    type_ids = {item.name: item.id for item in types}
    fields = [
        await master_data.create_field(
            admin.id,
            item.name,
            item.data_type,
            document_type_ids=(
                None
                if item.document_types is None
                else [type_ids[name] for name in item.document_types]
            ),
            choices=item.choices,
            required=item.required,
            required_document_type_ids=[type_ids[name] for name in item.required_types],
            description=item.description,
            assignment=item.assignment,
        )
        for item in spec.fields
    ]
    return MasterData(
        contacts={item.id: item.name for item in contacts},
        document_types={item.id: item.name for item in types},
        tags={item.id: item.name for item in tags},
        fields={item.id: item for item in fields},
        topics={id: name for name, id in topics.items()},
    )


async def _take_over(
    environment: Environment,
    corpus: Sequence[TrainingDocument],
    master: MasterData,
    admin: User,
    owner: User,
) -> None:
    """Take the corpus over with its metadata (as the migration does: it counts as confirmed),
    a topic set by a person afterwards (Paperless knows none), index it and train the
    classifier on it."""
    assert environment.indexing is not None and environment.classifier is not None
    pipeline = environment.pipeline_for(None)
    contacts = _ids(master.contacts)
    types = _ids(master.document_types)
    tags = _ids(master.tags)
    for item in corpus:
        imported = ImportedMetadata(
            title=item.name,
            contact_id=None if item.contact is None else contacts[item.contact],
            document_type_id=None if item.document_type is None else types[item.document_type],
            tag_ids=frozenset(tags[name] for name in item.tags),
            document_date=_date(item.document_date),
        )
        received = await _receive(
            pipeline,
            environment,
            f"training {item.name}",
            item.text,
            actor=admin.id,
            filename=f"{item.name}.pdf",
            channel=Channel.MIGRATION,
            owner=owner.id,
            imported=imported,
        )
        await _drain(pipeline)
        if item.topic is not None:
            topic = _ids(master.topics)[item.topic]
            await environment.documents.change_metadata(
                owner.id, received.id, DocumentChanges(topic_id=topic)
            )
        await environment.indexing.index_document(received.id)
    await environment.classifier.train()


async def _receive(
    pipeline: PipelineService,
    environment: Environment,
    marker: str,
    text: str,
    *,
    actor: UserId,
    filename: str,
    channel: Channel = Channel.API,
    owner: UserId | None = None,
    imported: ImportedMetadata | None = None,
) -> Document:
    """Receive a document and store its text as the parse node would have."""
    with tempfile.TemporaryDirectory(prefix="papiq-evaluation-") as directory:
        path = Path(directory) / "case.pdf"
        # A distinct file per document: the owner cannot have the same file twice.
        path.write_bytes(f"%PDF-1.7\n% evaluation {marker}\n%%EOF\n".encode())
        received = await pipeline.receive(
            actor,
            IncomingFile.of(path),
            filename=filename,
            channel=channel,
            owner=owner,
            imported=imported,
        )
    await environment.store.put(
        text_key(received.id),
        text.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
    )
    return received


async def _drain(pipeline: PipelineService) -> None:
    for _ in range(_MAX_JOBS):
        if not await pipeline.run_next_job():
            break


async def _run(
    case: Case, environment: Environment, master: MasterData, owner: User, filename: str
) -> CaseResult:
    pipeline = environment.pipeline_for(case)
    received = await _receive(
        pipeline, environment, f"case {case.name}", case.text, actor=owner.id, filename=filename
    )
    started = time.perf_counter()
    await _drain(pipeline)
    seconds = time.perf_counter() - started
    document = await environment.documents.get(owner.id, received.id)
    log = await environment.documents.processing_log(owner.id, received.id)
    return replace(_result(case, received, document, log, master, seconds), run=environment.run)


async def _corrected(
    result: CaseResult, environment: Environment, master: MasterData, owner: User
) -> CaseResult:
    """A person sets the expected values: confirming a yellow document in the inbox, or
    changing a green one. A red one is left alone. What they corrected is read from the log,
    as `corrected` of their entry."""
    automatic = frozenset(
        name
        for name, check in result.checks.items()
        if check.ok and (name != TOPIC or check.value is not None)  # a topic may be suggested
    )
    result = replace(result, automatic=automatic)
    if result.lane not in (Lane.GREEN, Lane.YELLOW):
        return result
    id = result.document_id
    document = await environment.documents.get(owner.id, id)
    if result.lane is Lane.YELLOW and result.status is ProcessingStatus.REVIEW:
        log = await environment.documents.processing_log(owner.id, id)
        open = {check.field for item in open_nodes(document, log) for check in item.fields}
        pipeline = environment.pipeline_for(result.case)
        changes = _expected_changes(result.case.expected, document, master, decide=open)
        resume = Node.APPLY_RULES
        if changes.document_type_id is not UNSET:
            # As the web UI does: the fields of the corrected type are read again, so no
            # value is given for them, except those set by hand (never read).
            kept = {
                id: value for id, value in changes.fields.items() if not master.fields[id].is_read
            }
            changes, resume = replace(changes, fields=kept), Node.FIELDS
        await pipeline.confirm(owner.id, id, changes, resume_at=resume)
        await _drain(pipeline)
    else:
        changes = _expected_changes(result.case.expected, document, master)
        if changes != DocumentChanges():
            await environment.documents.change_metadata(owner.id, id, changes)
    corrected = {
        _field_label(name, master)
        for entry in await environment.documents.processing_log(owner.id, id)
        for record in (entry.result.input, entry.result.output)
        for name in _strings(record.get("corrected"))
    }
    return replace(result, corrected=frozenset(corrected))


def _expected_changes(
    expected: Expected,
    document: Document,
    master: MasterData,
    *,
    decide: frozenset[str] | set[str] = frozenset(),
) -> DocumentChanges:
    """What the document needs to hold the expected values: every value that differs, the
    fields it has that are not expected removed, and every field in `decide` (open in the
    inbox) given, also where it stays."""
    contact = None if expected.contact is None else _ids(master.contacts)[expected.contact]
    kind = (
        None
        if expected.document_type is None
        else _ids(master.document_types)[expected.document_type]
    )
    tags = frozenset(_ids(master.tags)[name] for name in expected.tags)
    topic = None if expected.topic is None else _ids(master.topics)[expected.topic]
    dated = _date(expected.document_date)
    fields: dict[FieldId, object] = {}
    for definition in master.fields.values():
        value = expected.fields.get(definition.name)
        wanted = None if value is None else field_from_json(definition, value)
        if field_key(definition.id) in decide or document.fields.get(definition.id) != wanted:
            fields[definition.id] = wanted
    changes = DocumentChanges(fields=fields)
    if document.contact_id != contact or CONTACT in decide:
        changes = replace(changes, contact_id=contact)
    if document.document_type_id != kind or DOCUMENT_TYPE in decide:
        changes = replace(changes, document_type_id=kind)
    if document.topic_id != topic or TOPIC in decide:
        changes = replace(changes, topic_id=topic)
    if frozenset(document.tag_ids) != tags or TAGS in decide:
        changes = replace(changes, tag_ids=tags)
    if document.document_date != dated or DOCUMENT_DATE in decide:
        changes = replace(changes, document_date=dated)
    return changes


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _ids[K](names: Mapping[K, str]) -> dict[str, K]:
    return {name: id for id, name in names.items()}


def _result(
    case: Case,
    before: Document,
    after: Document,
    log: list[NodeRun],
    master: MasterData,
    seconds: float,
) -> CaseResult:
    by_hand = {field_key(item.id) for item in master.fields.values() if not item.is_read}
    checks = {
        _field_label(field, master): check
        for field, check in field_checks(log).items()
        if field not in by_hand
    }
    truncated = any(
        entry.result.input.get("truncated") is True
        for entry in log
        if entry.node in (Node.JUDGE, Node.FIELDS)
    )
    return CaseResult(
        case=case,
        document_id=after.id,
        owner_id=after.owner_id,
        lane=after.lane,
        status=after.processing.status,
        contact=None if after.contact_id is None else master.contacts[after.contact_id],
        document_type=(
            None
            if after.document_type_id is None
            else master.document_types[after.document_type_id]
        ),
        tags=frozenset(master.tags[tag] for tag in after.tag_ids),
        topic=None if after.topic_id is None else master.topics[after.topic_id],
        document_date=None if after.document_date is None else after.document_date.isoformat(),
        fields={master.fields[id].name: field_to_json(value) for id, value in after.fields.items()},
        checks=checks,
        proposed={label: _resolved(check, master) for label, check in checks.items()},
        reasons={item.node: item.reason or "" for item in open_nodes(after, log)},
        seconds=seconds,
        violations=_violations(before, after, field_checks(log)),
        truncated=truncated,
        by_hand=frozenset(item.name for item in master.fields.values() if not item.is_read),
    )


def _resolved(check: FieldCheck, master: MasterData) -> JsonValue:
    """The checked value, or else the suggestion (a topic's too: it is always OK), with
    contact, type, topic and tag ids as names."""
    value = check.value if check.ok and check.value is not None else check.suggestion
    names: dict[str, str]
    match check.field:
        case "contact":
            names = {str(id): name for id, name in master.contacts.items()}
        case "document_type":
            names = {str(id): name for id, name in master.document_types.items()}
        case "topic":
            names = {str(id): name for id, name in master.topics.items()}
        case "tags":
            names = {str(id): name for id, name in master.tags.items()}
            tags: list[JsonValue] = []
            tags.extend(sorted(names.get(item, item) for item in _strings(value)))
            return tags
        case _:
            return value
    return names.get(value, value) if isinstance(value, str) else value


def _field_label(field: str, master: MasterData) -> str:
    for id, definition in master.fields.items():
        if field == field_key(id):
            return definition.name
    return field


def _violations(
    before: Document, after: Document, checks: dict[str, FieldCheck]
) -> tuple[str, ...]:
    """Changes the checks do not back: every changed field must have an OK check with the
    applied value; space and owner must stay."""
    problems = []
    for name in ("owner_id", "space_id"):
        if getattr(before, name) != getattr(after, name):
            problems.append(f"{name} changed")

    def backed(field: str, value: JsonValue) -> bool:
        check = checks.get(field)
        return check is not None and check.ok and check.value == value

    if after.title != before.title and not backed(TITLE, after.title):
        problems.append("title changed without a passed check")
    if after.contact_id != before.contact_id and not backed(
        "contact", None if after.contact_id is None else str(after.contact_id)
    ):
        problems.append("contact changed without a passed check")
    if after.document_type_id != before.document_type_id and not backed(
        "document_type", None if after.document_type_id is None else str(after.document_type_id)
    ):
        problems.append("document type changed without a passed check")
    if after.topic_id != before.topic_id and not backed(
        "topic", None if after.topic_id is None else str(after.topic_id)
    ):
        problems.append("topic changed without a passed check")
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
    for id, value in after.fields.items():
        if before.fields.get(id) != value and not backed(field_key(id), field_to_json(value)):
            problems.append(f"field {id} changed without a passed check")
    return tuple(problems)


def _strings(value: JsonValue) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

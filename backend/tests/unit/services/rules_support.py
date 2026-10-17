"""Rules on in-memory adapters: the services wired as `build_services` wires them, a pattern
matcher whose patterns can time out, a classification that sets values like the model, and
builders for rule definitions."""

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from papiq.core.domain.classification import FieldCheck, checks_to_json
from papiq.core.domain.documents import Channel, Document, DocumentChanges
from papiq.core.domain.errors import PatternTimeoutError
from papiq.core.domain.ids import ContactId, DocumentTypeId, SpaceId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Contact, Tag
from papiq.core.domain.pipeline import Node, NodeResult, NodeRun, Outcome
from papiq.core.domain.rules import (
    Action,
    AddTags,
    Condition,
    ConditionField,
    Group,
    Operator,
    Rule,
    RuleDefinition,
    RuleScope,
    Trigger,
)
from papiq.core.domain.users import Role, User
from papiq.core.services.documents import DocumentService
from papiq.core.services.objects import text_key
from papiq.core.services.pipeline import (
    IncomingFile,
    MetadataResult,
    NodeExecutor,
    PipelineService,
)
from papiq.core.services.rules import RuleService
from papiq.core.services.rules.changes import ChangeRules
from papiq.core.services.rules.nodes import ApplyRulesNode, FileNode
from papiq.core.services.rules.retroactive import RuleApplicationService
from tests.builders import TEMPLATES, incoming
from tests.unit.services.conftest import World

MAX_TEXT = 10_000
MAX_DOCUMENTS = 100


class FakeMatcher:
    """`re` without a time limit; the patterns in `slow` time out."""

    def __init__(self) -> None:
        self.slow: set[str] = set()
        self.searched: list[str] = []

    async def search(self, pattern: str, text: str, *, case_sensitive: bool) -> bool:
        self.searched.append(pattern)
        if pattern in self.slow:
            raise PatternTimeoutError(f"pattern {pattern!r} took longer than 0.2 s")
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.search(pattern, text, flags) is not None


class Classifies:
    """Classification that sets a contact, type, topic and tags as the model does: applied,
    unconfirmed."""

    def __init__(
        self,
        *,
        contact: ContactId | None = None,
        tags: Iterable[TagId] = (),
        document_type: DocumentTypeId | None = None,
        topic: TagId | None = None,
    ) -> None:
        self.contact = contact
        self.tags = frozenset(tags)
        self.document_type = document_type
        self.topic = topic

    async def run(self, document: Document) -> MetadataResult:
        checks: list[FieldCheck] = []
        changes = DocumentChanges()
        if self.contact is not None:
            checks.append(
                FieldCheck(
                    field="contact", outcome=Outcome.OK, confidence=0.9, value=str(self.contact)
                )
            )
            changes = DocumentChanges(contact_id=self.contact)
        if self.document_type is not None:
            checks.append(
                FieldCheck(
                    field="document_type",
                    outcome=Outcome.OK,
                    confidence=0.9,
                    value=str(self.document_type),
                )
            )
            changes = replace(changes, document_type_id=self.document_type)
        if self.topic is not None:
            checks.append(
                FieldCheck(field="topic", outcome=Outcome.OK, confidence=1, value=str(self.topic))
            )
            changes = replace(changes, topic_id=self.topic)
        tags: list[JsonValue] = [str(tag) for tag in sorted(self.tags)]
        checks.append(FieldCheck(field="tags", outcome=Outcome.OK, confidence=0.9, value=tags))
        result = NodeResult(
            outcome=Outcome.OK,
            model_version="fake-llm 1",
            output={"fields": checks_to_json(checks)},
        )
        return MetadataResult(result, changes, add_tags=self.tags)


class Titles:
    """The fields node giving the document a title, as the machine does."""

    def __init__(self, title: str) -> None:
        self.title = title

    async def run(self, document: Document) -> MetadataResult:
        check = FieldCheck(field="title", outcome=Outcome.OK, confidence=1, value=self.title)
        result = NodeResult(
            outcome=Outcome.OK,
            model_version="fake-llm 1",
            output={"fields": checks_to_json([check])},
        )
        return MetadataResult(result, DocumentChanges(title=self.title))


# --- rule definitions ---------------------------------------------------------------------------


def all_of(*items: Condition | Group, negate: bool = False) -> Group:
    return Group(mode="all", items=items, negate=negate)


def channel_api() -> Condition:
    """Holds for every document uploaded through the API (the default channel)."""
    return Condition(field=ConditionField.CHANNEL, op=Operator.IS, value=Channel.API.value)


def contact_is(contact: ContactId) -> Condition:
    return Condition(field=ConditionField.CONTACT, op=Operator.IS, value=str(contact))


def add_tags(*tags: TagId) -> AddTags:
    return AddTags(frozenset(tags))


def has_tag(tag: TagId) -> Condition:
    return Condition(field=ConditionField.TAGS, op=Operator.CONTAINS, value=str(tag))


def text_matches(pattern: str) -> Condition:
    return Condition(field=ConditionField.TEXT, op=Operator.MATCHES, value=pattern)


def definition(
    name: str,
    conditions: Condition | Group,
    *actions: Action,
    triggers: Iterable[Trigger] = (Trigger.INGEST, Trigger.CHANGE),
    priority: int = 100,
) -> RuleDefinition:
    return RuleDefinition(
        name=name,
        conditions=conditions if isinstance(conditions, Group) else all_of(conditions),
        actions=actions,
        triggers=frozenset(triggers),
        priority=priority,
    )


# --- the world ----------------------------------------------------------------------------------


@dataclass
class RuleWorld:
    """`World` with the rule services, an admin for master data and global rules, and a pattern
    matcher under the test's control."""

    world: World
    admin: User
    matcher: FakeMatcher = field(default_factory=FakeMatcher)

    @property
    def rules(self) -> RuleService:
        return RuleService(self.world.uow, self.world.clock, TEMPLATES)

    @property
    def applications(self) -> RuleApplicationService:
        return RuleApplicationService(
            self.world.uow,
            self.world.clock,
            self.world.object_store,
            self.matcher,
            TEMPLATES,
            max_text=MAX_TEXT,
            max_documents=MAX_DOCUMENTS,
            pipeline_version="test",
        )

    @property
    def documents(self) -> DocumentService:
        change_rules = ChangeRules(
            self.world.object_store,
            self.matcher,
            TEMPLATES,
            max_text=MAX_TEXT,
            pipeline_version="test",
        )
        return DocumentService(
            self.world.uow, self.world.clock, self.world.object_store, rules=change_rules
        )

    def pipeline(
        self, classify: NodeExecutor | None = None, fields: NodeExecutor | None = None
    ) -> PipelineService:
        executors: dict[Node, NodeExecutor] = {
            Node.APPLY_RULES: ApplyRulesNode(
                self.world.uow,
                self.world.object_store,
                self.matcher,
                TEMPLATES,
                max_text=MAX_TEXT,
            ),
            Node.FILE: FileNode(),
        }
        if classify is not None:
            executors[Node.JUDGE] = classify
        if fields is not None:
            executors[Node.FIELDS] = fields
        return self.world.pipeline(executors)

    # --- seeding ---------------------------------------------------------------------------------

    async def contact(self, name: str) -> Contact:
        return await self.world.master_data.create_contact(self.admin.id, name)

    async def tag(self, name: str) -> Tag:
        return await self.world.master_data.create_tag(self.admin.id, name)

    async def user_rule(self, owner: User, rule: RuleDefinition) -> Rule:
        return await self.rules.create(owner.id, RuleScope.USER, rule)

    async def global_rule(self, rule: RuleDefinition) -> Rule:
        return await self.rules.create(self.admin.id, RuleScope.GLOBAL, rule)

    async def arrive(
        self,
        owner: User,
        *,
        space: SpaceId | None = None,
        text: str | None = None,
        classify: NodeExecutor | None = None,
        fields: NodeExecutor | None = None,
        filename: str = "scan.pdf",
    ) -> Document:
        """Upload a document, give it `text` as its parsed Markdown, process it; the stored
        document."""
        pipeline = self.pipeline(classify, fields)
        received = await pipeline.receive(owner.id, incoming_pdf(), filename=filename, space=space)
        if text is not None:
            await self.world.object_store.put(
                text_key(received.id), text.encode(), content_type="text/markdown"
            )
        await self.world.drain(pipeline)
        return await self.stored(received)

    async def stored(self, document: Document) -> Document:
        async with self.world.uow() as uow:
            return await uow.documents.get(document.id)

    async def log(self, document: Document) -> list[NodeRun]:
        async with self.world.uow() as uow:
            return await uow.processing_log.list_for(document.id)

    async def rule_entries(self, document: Document, model_version: str) -> list[NodeRun]:
        return [
            entry
            for entry in await self.log(document)
            if entry.node is Node.APPLY_RULES and entry.result.model_version == model_version
        ]

    async def deactivate(self, user: User) -> None:
        await self.world.users.set_active(self.admin.id, user.id, False)


def incoming_pdf() -> IncomingFile:
    """A PDF file with unique content."""
    return incoming(b"%PDF-1.7\n" + uuid.uuid4().hex.encode())


async def rule_world(world: World) -> RuleWorld:
    return RuleWorld(world, await world.user(role=Role.ADMIN))


def reports(output: JsonObject) -> list[JsonObject]:
    """The rule reports of a log entry's output."""
    found = output.get("rules", [])
    assert isinstance(found, list)
    return [item for item in found if isinstance(item, dict)]


def notes(output: JsonObject) -> list[tuple[str, str]]:
    """(field, kind) of every note in a log entry's output."""
    found: list[tuple[str, str]] = []
    for report in reports(output):
        items = report.get("notes", [])
        assert isinstance(items, list)
        found.extend(
            (str(item["field"]), str(item["kind"])) for item in items if isinstance(item, dict)
        )
    return found

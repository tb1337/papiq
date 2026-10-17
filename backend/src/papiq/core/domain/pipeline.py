"""Ingest graph: nodes and their dependencies, node results, the state of each node, lanes."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.validation import require_utc


class Node(StrEnum):
    """The nodes of the ingest graph, in a topological order: every node comes after the nodes
    it needs (`GRAPH`). Nodes that do not need each other run side by side.

    The signals each propose metadata from one source: the language model, the trained
    classifier, similar confirmed documents, facts in the text (names, identifiers). `judge`
    decides from all of them, `fields` reads the fields of the decided type."""

    RECEIVE = "receive"
    OCR = "ocr"
    PAGES = "pages"
    PARSE = "parse"
    SIGNAL_LLM = "signal_llm"
    SIGNAL_CLASSIFIER = "signal_classifier"
    SIGNAL_NEIGHBOURS = "signal_neighbours"
    SIGNAL_FACTS = "signal_facts"
    JUDGE = "judge"
    FIELDS = "fields"
    APPLY_RULES = "apply_rules"
    FILE = "file"

    @property
    def requires(self) -> frozenset["Node"]:
        """The nodes whose results this node needs."""
        return GRAPH[self]

    @property
    def successors(self) -> frozenset["Node"]:
        """The nodes that need this node's result."""
        return frozenset(node for node, needed in GRAPH.items() if self in needed)

    @property
    def ancestors(self) -> frozenset["Node"]:
        """Every node this node depends on, directly or through others."""
        return _closure(self, lambda node: node.requires)

    @property
    def descendants(self) -> frozenset["Node"]:
        """Every node that depends on this node, directly or through others."""
        return _closure(self, lambda node: node.successors)


SIGNALS: frozenset["Node"] = frozenset(
    {Node.SIGNAL_LLM, Node.SIGNAL_CLASSIFIER, Node.SIGNAL_NEIGHBOURS, Node.SIGNAL_FACTS}
)
"""The nodes that propose metadata for the judge; they never decide the lane themselves."""

CURRENT_SIGNALS: frozenset["Node"] = frozenset(
    {Node.SIGNAL_CLASSIFIER, Node.SIGNAL_NEIGHBOURS, Node.SIGNAL_FACTS}
)
"""The signals that read the current master data, model and index without asking a language
model: a reprocessing from the judge runs them again, so a new alias, identifier or model takes
effect. The language model's signal keeps its answer."""

GRAPH: Mapping[Node, frozenset[Node]] = {
    Node.RECEIVE: frozenset(),
    Node.OCR: frozenset({Node.RECEIVE}),
    Node.PAGES: frozenset({Node.RECEIVE}),
    Node.PARSE: frozenset({Node.OCR}),
    **dict.fromkeys(SIGNALS, frozenset({Node.PARSE})),
    Node.JUDGE: SIGNALS,
    Node.FIELDS: frozenset({Node.JUDGE}),
    Node.APPLY_RULES: frozenset({Node.FIELDS}),
    Node.FILE: frozenset({Node.APPLY_RULES, Node.PAGES}),
}
"""What each node needs. `receive` is the source, `file` the only sink: every other node is
one of its ancestors, so filing is the last thing that happens to a document."""

NODES: tuple[Node, ...] = tuple(Node)
"""All nodes in topological order (the order of the enum)."""


def _closure(start: Node, step: Callable[[Node], Iterable[Node]]) -> frozenset[Node]:
    found: set[Node] = set()
    pending = list(step(start))
    while pending:
        node = pending.pop()
        if node not in found:
            found.add(node)
            pending.extend(step(node))
    return frozenset(found)


class Outcome(StrEnum):
    OK = "ok"
    UNCERTAIN = "uncertain"  # a person has to confirm
    FAILED = "failed"  # after all retries; a person has to intervene


class Lane(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    @classmethod
    def from_outcomes(cls, outcomes: Iterable[Outcome]) -> "Lane":
        """The worst outcome decides: any failure is red, any uncertainty yellow."""
        found = set(outcomes)
        if Outcome.FAILED in found:
            return cls.RED
        if Outcome.UNCERTAIN in found:
            return cls.YELLOW
        return cls.GREEN


@dataclass(frozen=True, kw_only=True)
class NodeResult:
    """What a node decided. A reason is required unless the node went through cleanly."""

    outcome: Outcome
    reason: str | None = None
    confidence: float | None = None  # 0..1, derived from verifiable facts where possible
    model_version: str | None = None
    input: JsonObject = field(default_factory=dict)
    output: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is not Outcome.OK and not (self.reason and self.reason.strip()):
            raise ValidationError(f"a {self.outcome} node result needs a reason")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValidationError(f"confidence must be between 0 and 1, got {self.confidence}")


@dataclass(frozen=True, kw_only=True)
class NodeRun:
    """Processing log entry: one execution of one node for one document."""

    document_id: DocumentId
    node: Node
    run: int
    result: NodeResult
    pipeline_version: str
    started_at: datetime
    duration: timedelta

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", require_utc(self.started_at, "started_at"))
        if self.duration < timedelta(0):
            raise ValidationError("duration must not be negative")
        if self.run < 1:
            raise ValidationError("run starts at 1")


# Entries that people and rules write carry these as `model_version`, in place of a model.
PERSON = "person"
"""`model_version` of a log entry in which the owner confirmed a node's results."""
RULES = "rules"
"""`model_version` of the log entry of the pipeline node `apply_rules`."""
RULES_CHANGE = "rules:change"
"""`model_version` of the `apply_rules` log entry of a metadata change by a person (with the
rules it set off)."""
RULES_APPLY = "rules:apply"
"""`model_version` of the `apply_rules` log entry of a rule applied to an existing document on
request."""
PERSON_SPACE = "person:space"
"""`model_version` of a log entry in which a person chose the space outside the inbox (an
upload into a given space, a move); rules leave the space then."""
CHOSEN_SPACE = "space_id"
"""Key of the space a person chose, in the input of a `PERSON_SPACE` entry and the output of a
`PERSON` entry."""
OUTSIDE_PIPELINE = frozenset({RULES_CHANGE, RULES_APPLY, PERSON_SPACE})
"""Log entries written outside processing; they say nothing about a node's state."""


def space_chooser(log: Sequence[NodeRun], space: UUID) -> UUID | None:
    """Who last put the document into `space` by hand (confirming, uploading into a given
    space, moving), if anyone. Entries written before the space was recorded name none."""
    for entry in reversed(log):
        result = entry.result
        if result.model_version == PERSON_SPACE:
            actor, chosen = result.input.get("actor"), result.input.get(CHOSEN_SPACE)
        elif result.model_version == PERSON:
            actor, chosen = result.output.get("confirmed_by"), result.output.get(CHOSEN_SPACE)
        else:
            continue
        if chosen == str(space):
            return UUID(actor) if isinstance(actor, str) else None
    return None


class NodeStatus(StrEnum):
    WAITING = "waiting"  # not run yet in this processing run
    RUNNING = "running"  # a worker is at it
    DONE = "done"  # has an outcome
    SKIPPED = "skipped"  # left out in this processing run; counts as done for the nodes after it


@dataclass(kw_only=True)
class NodeState:
    """The state of one node in the current processing run."""

    status: NodeStatus = NodeStatus.WAITING
    outcome: Outcome | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.started_at is not None:
            self.started_at = require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            self.finished_at = require_utc(self.finished_at, "finished_at")
        if (self.status is NodeStatus.DONE) != (self.outcome is not None):
            raise ValidationError("exactly the done nodes have an outcome")
        if self.status is NodeStatus.RUNNING and self.started_at is None:
            raise ValidationError("a running node has a start")
        if self.status is NodeStatus.DONE and self.finished_at is None:
            raise ValidationError("a done node has an end")

    @property
    def satisfied(self) -> bool:
        """True if the nodes after this one may run: it is done without failing, or skipped."""
        if self.status is NodeStatus.SKIPPED:
            return True
        return self.status is NodeStatus.DONE and self.outcome is not Outcome.FAILED

    @property
    def open(self) -> bool:
        """True while the node is still to run or running."""
        return self.status in (NodeStatus.WAITING, NodeStatus.RUNNING)


class ProcessingStatus(StrEnum):
    PROCESSING = "processing"  # nodes are due or running
    COMPLETED = "completed"  # every node done or skipped
    FAILED = "failed"  # a node failed; running nodes finish, nothing new starts
    REVIEW = "review"  # uncertain results; filing waits for the owner


@dataclass(kw_only=True)
class Processing:
    """Processing state of a document: the state of every node of the graph.

    `run` counts processing runs; it grows with every retry or reprocessing, so jobs of an
    earlier run are recognised as stale. `nodes` holds the state of every node in the current
    run. `vision`: a person asked that this run read the pages with the vision language model
    (`Document.read_with_vision`); a retry keeps it, another reprocessing does not.
    """

    status: ProcessingStatus
    run: int
    nodes: dict[Node, NodeState]
    vision: bool = False

    def __post_init__(self) -> None:
        missing = [node.value for node in NODES if node not in self.nodes]
        if missing:
            raise ValidationError(f"processing state without nodes: {', '.join(missing)}")
        # In graph order, whatever order the store returned them in (JSONB sorts keys).
        self.nodes = {node: self.nodes[node] for node in NODES}
        if self.run < 1:
            raise ValidationError("run starts at 1")
        if (self.status is ProcessingStatus.COMPLETED) == self.is_open():
            raise ValidationError("exactly the completed processing has no open node")
        if self.status is ProcessingStatus.FAILED and not self.failed():
            raise ValidationError("failed processing names a failed node")
        if self.status is ProcessingStatus.REVIEW and not self.nodes[Node.FILE].open:
            raise ValidationError("processing in review has filing open")

    @classmethod
    def new(cls) -> "Processing":
        """The state of a document that just arrived: every node waiting."""
        return cls(
            status=ProcessingStatus.PROCESSING, run=1, nodes={node: NodeState() for node in NODES}
        )

    def outcome(self, node: Node) -> Outcome | None:
        return self.nodes[node].outcome

    @property
    def outcomes(self) -> dict[Node, Outcome]:
        """The outcomes of the done nodes, in graph order."""
        return {
            node: state.outcome for node, state in self.nodes.items() if state.outcome is not None
        }

    def due(self) -> tuple[Node, ...]:
        """The waiting nodes whose needs are all met, in graph order. Whether they may start
        depends on the status (`Document.is_awaiting`)."""
        return tuple(
            node
            for node, state in self.nodes.items()
            if state.status is NodeStatus.WAITING
            and all(self.nodes[needed].satisfied for needed in node.requires)
        )

    def running(self) -> tuple[Node, ...]:
        return self._with(lambda state: state.status is NodeStatus.RUNNING)

    def failed(self) -> tuple[Node, ...]:
        return self._with(lambda state: state.outcome is Outcome.FAILED)

    def _with(self, condition: Callable[[NodeState], bool]) -> tuple[Node, ...]:
        return tuple(node for node, state in self.nodes.items() if condition(state))

    def is_open(self) -> bool:
        """True while any node is still to run or running."""
        return any(state.open for state in self.nodes.values())

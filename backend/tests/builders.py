"""Test data builders shared by unit tests and contract suites."""

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from papiq.adapters.outbound.liquid import LiquidTemplateRenderer
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.ids import DocumentId, SpaceId, UserId, new_id
from papiq.core.domain.pipeline import Lane, Node, NodeResult, Outcome
from papiq.core.domain.search import IndexDocument
from papiq.core.domain.spaces import Space
from papiq.core.domain.users import Role, User
from papiq.core.services.pipeline import IncomingFile, PipelineService, PlaceholderNode

# Templates of rules and title patterns, as the memory container renders them.
TEMPLATES = LiquidTemplateRenderer(locale="de", currency="EUR")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

# PAPIQ_SECRET_KEY for tests (32 bytes, base64); never use it elsewhere.
SECRET_KEY = "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktdGVzdC0="
PASSWORD = "correct horse battery"
# PAPIQ_FORWARDED_ALLOW_IPS for tests: required with secure cookies.
TRUSTED_PROXY = "127.0.0.1"

OK = NodeResult(outcome=Outcome.OK)
UNCERTAIN = NodeResult(outcome=Outcome.UNCERTAIN, reason="new contact", confidence=0.4)
FAILED = NodeResult(outcome=Outcome.FAILED, reason="OCR crashed")

_counter = 0


def _unique(prefix: str) -> str:
    global _counter
    _counter += 1
    return f"{prefix}-{_counter}"


def user(name: str | None = None, *, role: Role = Role.USER) -> User:
    return User.create(username=name or _unique("user"), role=role, now=NOW)


def admin(name: str | None = None) -> User:
    return user(name, role=Role.ADMIN)


def space(owner: User, name: str | None = None) -> Space:
    return Space.create(owner_id=owner.id, name=name or _unique("space"), now=NOW)


def default_space(owner: User) -> Space:
    return Space.create_default(owner_id=owner.id, now=NOW)


def sha256(content: str | None = None) -> Sha256:
    return Sha256.of((content or _unique("content")).encode())


def document(
    owner: User | UserId,
    in_space: Space | SpaceId,
    *,
    content: str | None = None,
    filename: str = "scan.pdf",
) -> Document:
    """A freshly received document; OCR is due."""
    document = Document.receive(
        owner_id=owner.id if isinstance(owner, User) else owner,
        space_id=in_space.id if isinstance(in_space, Space) else in_space,
        sha256=sha256(content),
        original_filename=filename,
        media_type="application/pdf",
        result=OK,
        now=NOW,
    )
    document.pull_events()
    return document


def run_pipeline(document: Document, results: dict[Node, NodeResult] | None = None) -> Document:
    """Run the due nodes with OK (or the given results), one at a time in graph order, until
    processing stops."""
    results = results or {}
    while True:
        run = document.processing.run
        due = [node for node in document.processing.due() if document.is_awaiting(node, run)]
        if not due:
            return document
        document.record_result(due[0], run, results.get(due[0], OK), NOW)


def processed(
    owner: User,
    in_space: Space,
    results: dict[Node, NodeResult] | None = None,
    *,
    content: str | None = None,
) -> Document:
    """A document whose pipeline has run; green unless `results` say otherwise."""
    result = run_pipeline(document(owner, in_space, content=content), results)
    result.pull_events()
    return result


# Received files of the tests; the directory is removed when the test process ends.
_INCOMING = tempfile.TemporaryDirectory(prefix="papiq-tests-")


def incoming(data: bytes) -> IncomingFile:
    """`data` as a received file, ready for `PipelineService.receive`."""
    path = Path(_INCOMING.name) / uuid.uuid4().hex
    path.write_bytes(data)
    return IncomingFile.of(path)


def skip_classification(pipeline: PipelineService) -> PipelineService:
    """Let classification and field extraction pass as OK, for tests about other things
    (without a language model, they would make every document yellow)."""
    for node in (Node.JUDGE, Node.FIELDS):
        pipeline._executors[node] = PlaceholderNode()
    return pipeline


def index_document(**fields: Any) -> IndexDocument:
    """A green document of a random owner in a random space, for the search index; `fields`
    override any of its fields."""
    values: dict[str, Any] = {
        "id": DocumentId(new_id()),
        "version": 1,
        "owner_id": UserId(new_id()),
        "space_id": SpaceId(new_id()),
        "lane": Lane.GREEN,
        "title": _unique("title"),
        "filename": "scan.pdf",
        "created_at": NOW,
    }
    values.update(fields)
    return IndexDocument(**values)

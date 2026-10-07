"""Test data builders shared by unit tests and contract suites."""

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.ids import DrawerId, UserId
from papiq.core.domain.pipeline import Outcome, Step, StepResult
from papiq.core.domain.users import Role, User
from papiq.core.services.pipeline import IncomingFile

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

OK = StepResult(outcome=Outcome.OK)
UNCERTAIN = StepResult(outcome=Outcome.UNCERTAIN, reason="new contact", confidence=0.4)
FAILED = StepResult(outcome=Outcome.FAILED, reason="OCR crashed")

_counter = 0


def _unique(prefix: str) -> str:
    global _counter
    _counter += 1
    return f"{prefix}-{_counter}"


def user(name: str | None = None, *, role: Role = Role.USER) -> User:
    return User.create(username=name or _unique("user"), role=role, now=NOW)


def admin(name: str | None = None) -> User:
    return user(name, role=Role.ADMIN)


def drawer(owner: User, name: str | None = None) -> Drawer:
    return Drawer.create(owner_id=owner.id, name=name or _unique("drawer"), now=NOW)


def default_drawer(owner: User) -> Drawer:
    return Drawer.create_default(owner_id=owner.id, now=NOW)


def sha256(content: str | None = None) -> Sha256:
    return Sha256.of((content or _unique("content")).encode())


def document(
    owner: User | UserId,
    in_drawer: Drawer | DrawerId,
    *,
    content: str | None = None,
    filename: str = "scan.pdf",
) -> Document:
    """A freshly received document; OCR is due."""
    document = Document.receive(
        owner_id=owner.id if isinstance(owner, User) else owner,
        drawer_id=in_drawer.id if isinstance(in_drawer, Drawer) else in_drawer,
        sha256=sha256(content),
        original_filename=filename,
        media_type="application/pdf",
        result=OK,
        now=NOW,
    )
    document.pull_events()
    return document


def run_pipeline(document: Document, results: dict[Step, StepResult] | None = None) -> Document:
    """Run the remaining steps with OK (or the given results) until processing stops."""
    results = results or {}
    step = document.processing.current_step
    while step is not None and document.is_awaiting(step, document.processing.run):
        step = document.record_result(step, document.processing.run, results.get(step, OK), NOW)
    return document


def processed(
    owner: User,
    in_drawer: Drawer,
    results: dict[Step, StepResult] | None = None,
    *,
    content: str | None = None,
) -> Document:
    """A document whose pipeline has run; green unless `results` say otherwise."""
    result = run_pipeline(document(owner, in_drawer, content=content), results)
    result.pull_events()
    return result


# Received files of the tests; the directory is removed when the test process ends.
_INCOMING = tempfile.TemporaryDirectory(prefix="papiq-tests-")


def incoming(data: bytes) -> IncomingFile:
    """`data` as a received file, ready for `PipelineService.receive`."""
    path = Path(_INCOMING.name) / uuid.uuid4().hex
    path.write_bytes(data)
    return IncomingFile.of(path)

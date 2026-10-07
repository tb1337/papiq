import logging
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath

from papiq.core.domain import media_types
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import DocumentId, DrawerId, UserId
from papiq.core.domain.permissions import (
    can_move_document,
    can_read_document,
    document_access,
    is_document_owner,
)
from papiq.core.domain.pipeline import Lane, StepRun
from papiq.core.ports import Clock, DocumentFilter, ObjectStore, UnitOfWork, UnitOfWorkFactory
from papiq.core.ports.preview import PREVIEW_MEDIA_TYPE
from papiq.core.services._access import (
    check_references,
    load_actor,
    readable_document,
    visible_drawer,
    writable_document,
)
from papiq.core.services.inbox import InboxItem, Review, open_steps, step_reviews
from papiq.core.services.maintenance import REMOVE_FILES_JOB
from papiq.core.services.objects import archive_key, original_key, preview_key
from papiq.core.services.rules.changes import ChangeRules
from papiq.core.services.rules.running import Prepared, RuleRun

log = logging.getLogger(__name__)

MAX_PAGE = 200


@dataclass(frozen=True)
class MetadataChange:
    """A changed document; the caller's access afterwards (None if the rules filed it where
    the caller cannot see it); the rules' run, for the owner; the state before the change."""

    document: Document
    access: ShareLevel | None
    rules: RuleRun | None = None
    before: Document | None = None


@dataclass(frozen=True)
class DocumentView:
    """A document with the caller's access to it."""

    document: Document
    access: ShareLevel


class DocumentFile(StrEnum):
    ORIGINAL = "original"
    ARCHIVE = "archive"
    PREVIEW = "preview"


@dataclass(frozen=True)
class FileInfo:
    """A downloaded file: its media type and a filename to offer."""

    media_type: str
    filename: str


class DocumentService:
    """Reading and changing documents. Ingest and processing live in PipelineService."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        object_store: ObjectStore | None = None,
        *,
        rules: ChangeRules | None = None,
    ) -> None:
        """`rules`: run the change rules when a person changes a document's metadata."""
        self._uow = uow
        self._clock = clock
        self._store = object_store
        self._rules = rules

    async def get(self, actor: UserId, id: DocumentId) -> Document:
        async with self._uow() as uow:
            document, _ = await readable_document(uow, await load_actor(uow, actor), id)
            return document

    async def view(self, actor: UserId, id: DocumentId) -> DocumentView:
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, drawer = await readable_document(uow, user, id)
            access = document_access(user, document, drawer)
        assert access is not None  # readable
        return DocumentView(document, access)

    async def query(
        self,
        actor: UserId,
        filter: DocumentFilter,
        *,
        before: DocumentId | None = None,
        limit: int = 50,
    ) -> list[DocumentView]:
        """Readable documents matching `filter`, newest first, after the page ending at
        `before`. Each is checked against the permission rules once more."""
        limit = max(1, min(limit, MAX_PAGE))
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            documents = await uow.documents.query_visible(actor, filter, before=before, limit=limit)
            drawers: dict[DrawerId, Drawer] = {}
            views = []
            for document in documents:
                if document.drawer_id not in drawers:
                    drawers[document.drawer_id] = await uow.drawers.get(document.drawer_id)
                access = document_access(user, document, drawers[document.drawer_id])
                if access is None:
                    log.error(
                        "the repository listed a document the user may not read",
                        extra={"document_id": str(document.id), "user_id": str(actor)},
                    )
                    continue
                views.append(DocumentView(document, access))
        return views

    async def download(
        self, actor: UserId, id: DocumentId, file: DocumentFile, target: Path
    ) -> FileInfo:
        """Write a file of a readable document to the local path `target`. NotFoundError if
        the caller may not read the document or the file does not exist (yet)."""
        assert self._store is not None, "DocumentService needs an object store for downloads"
        async with self._uow() as uow:
            document, _ = await readable_document(uow, await load_actor(uow, actor), id)
        stem = PurePath(document.original_filename).stem or "document"
        match file:
            case DocumentFile.ORIGINAL:
                key = original_key(document.sha256)
                info = FileInfo(document.media_type, document.original_filename)
            case DocumentFile.ARCHIVE:
                key, info = archive_key(id), FileInfo(media_types.PDF, f"{stem}.pdf")
            case DocumentFile.PREVIEW:
                key, info = preview_key(id), FileInfo(PREVIEW_MEDIA_TYPE, f"{stem}.webp")
        if not await self._store.exists(key):
            raise NotFoundError(f"{file.value} of document", id)
        await self._store.download(key, target)
        return info

    async def list_visible(self, actor: UserId) -> list[Document]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.documents.list_visible_to(actor)

    async def filter_readers(self, id: DocumentId, candidates: Collection[UserId]) -> set[UserId]:
        """Those of `candidates` who may read the document now; none if it does not exist.
        For pushing events to users (SSE), so it reads the current state."""
        if not candidates:
            return set()
        async with self._uow() as uow:
            document = await uow.documents.find(id)
            if document is None:
                return set()
            drawer = await uow.drawers.get(document.drawer_id)
            readers: set[UserId] = set()
            for candidate in set(candidates):
                user = await uow.users.find(candidate)
                if user is not None and can_read_document(user, document, drawer):
                    readers.add(candidate)
            return readers

    async def processing_log(self, actor: UserId, id: DocumentId) -> list[StepRun]:
        """Owner only: the log may hold technical details of failed runs."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not is_document_owner(user, document):
                raise PermissionDeniedError(f"only the owner reads the processing log of {id}")
            return await uow.processing_log.list_for(id)

    async def inbox(
        self, actor: UserId, *, before: DocumentId | None = None, limit: int = 50
    ) -> list[InboxItem]:
        """The caller's yellow and red documents, newest first, with what is open in them."""
        limit = max(1, min(limit, MAX_PAGE))
        waiting = DocumentFilter(lanes=frozenset({Lane.YELLOW, Lane.RED}))
        async with self._uow() as uow:
            await load_actor(uow, actor)
            documents = await uow.documents.query_visible(
                actor, waiting, before=before, limit=limit
            )
            items = []
            for document in documents:
                if document.owner_id != actor:  # only owners see yellow and red documents
                    continue
                log = await uow.processing_log.list_for(document.id)
                items.append(InboxItem(document, open_steps(document, log)))
        return items

    async def review(self, actor: UserId, id: DocumentId) -> Review:
        """Owner only: what is open, and what the model proposed and how it was checked."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not is_document_owner(user, document):
                raise PermissionDeniedError(f"only the owner reviews document {id}")
            log = await uow.processing_log.list_for(id)
        return Review(document, open_steps(document, log), step_reviews(log))

    async def update_metadata(
        self, actor: UserId, id: DocumentId, changes: DocumentChanges
    ) -> Document:
        """Needs write access. Referenced contact, type, tags and attributes must exist."""
        return (await self.change_metadata(actor, id, changes)).document

    async def change_metadata(
        self, actor: UserId, id: DocumentId, changes: DocumentChanges, *, dry_run: bool = False
    ) -> MetadataChange:
        """As `update_metadata`, and the change rules of the document's owner and the global
        ones run (see `rules.changes`). The rules' report is for the owner only. With
        `dry_run`, nothing is stored: what would happen."""
        rules = self._rules
        prepared = Prepared()
        if rules is not None:
            async with self._uow() as uow:
                document, _ = await writable_document(uow, await load_actor(uow, actor), id)
                candidates = await rules.rules(uow, document)
            prepared = await rules.prepare(candidates, document)
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await writable_document(uow, user, id)
            await check_references(uow, changes)
            definitions = {item.id: item for item in await uow.attributes.list_all()}
            now = self._clock.now()
            before = None if rules is None else rules.snapshot(document)
            document.apply_changes(changes, definitions, now)
            run = None
            if rules is not None and before is not None:
                run = await rules.after_change(
                    uow,
                    actor=user,
                    before=before,
                    document=document,
                    changes=changes,
                    prepared=prepared,
                    definitions=definitions,
                    now=now,
                    log=not dry_run,
                )
            drawer = await uow.drawers.get(document.drawer_id)
            access = document_access(user, document, drawer)
            if not dry_run:
                await _save(uow, document)
                await uow.commit()
        owner = is_document_owner(user, document)
        return MetadataChange(document, access, run if owner else None, before)

    async def move(self, actor: UserId, id: DocumentId, drawer: DrawerId) -> None:
        """The owner moves into a drawer they may write to; an admin moves any document into
        any drawer. Moving grants the admin no read access, so nothing is returned."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if user.is_admin:
                document = await uow.documents.get(id)
                target = await uow.drawers.get(drawer)
            else:
                document, _ = await readable_document(uow, user, id)
                if not is_document_owner(user, document):
                    raise PermissionDeniedError(f"only the owner moves document {id}")
                target = await visible_drawer(uow, user, drawer)
            if not can_move_document(user, document, target):
                raise PermissionDeniedError(f"no write access to drawer {drawer}")
            document.move_to(target.id, self._clock.now())
            await _save(uow, document)
            await uow.commit()

    async def delete(self, actor: UserId, id: DocumentId) -> None:
        """Owner only. Removes the metadata and queues the removal of its files (derivatives,
        and the original unless another document has the same file)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not is_document_owner(user, document):
                raise PermissionDeniedError(f"only the owner deletes document {id}")
            document.delete(self._clock.now())
            await uow.documents.remove(id)
            await uow.outbox.add(document.pull_events())
            await uow.jobs.enqueue(
                REMOVE_FILES_JOB,
                {"document_id": str(id), "sha256": document.sha256.hex},
                run_at=self._clock.now(),
            )
            await uow.commit()


async def _save(uow: UnitOfWork, document: Document) -> None:
    await uow.documents.update(document)
    await uow.outbox.add(document.pull_events())

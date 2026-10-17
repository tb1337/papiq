import asyncio
import copy
import hashlib
import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath
from uuid import UUID

from papiq.core.domain import media_types
from papiq.core.domain.codes import Code, GiroCode, code_identifiers, codes_from_json, giro_code
from papiq.core.domain.documents import Document, DocumentChanges, confirms
from papiq.core.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    SpaceWriteDeniedError,
    UnprocessableDocumentError,
    ValidationError,
)
from papiq.core.domain.exchanges import RunExchange, exchanges_from_json
from papiq.core.domain.facts import Facts, facts_in
from papiq.core.domain.fields import FieldType, FieldValue
from papiq.core.domain.identifiers import IdentifierKind
from papiq.core.domain.ids import ContactId, DocumentId, FieldId, SpaceId, TagId, UserId
from papiq.core.domain.locations import Box, Location
from papiq.core.domain.master_data import Assignment
from papiq.core.domain.permissions import (
    can_control_document,
    can_manage_master_data,
    can_move_document,
    can_read_document,
    document_access,
    in_reach,
)
from papiq.core.domain.pipeline import Lane, Node, NodeRun
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User
from papiq.core.ports import (
    NEWEST_FIRST,
    Clock,
    CountBy,
    DocumentFilter,
    DocumentOrder,
    DocumentPosition,
    FileMetadata,
    MetadataReader,
    ObjectStore,
    SortKey,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.ports.pages import PAGE_MEDIA_TYPE
from papiq.core.services._access import (
    checked_changes,
    load_actor,
    readable_document,
    visible_space,
    writable_document,
)
from papiq.core.services._tags import expanded, tag_counts
from papiq.core.services.inbox import InboxItem, Review, node_reviews, open_nodes
from papiq.core.services.maintenance import REMOVE_FILES_JOB
from papiq.core.services.objects import (
    EXCHANGE_NODES,
    archive_key,
    codes_key,
    exchanges_key,
    legacy_exchanges_key,
    load_text,
    original_key,
    page_key,
    preview_key,
    structure_key,
)
from papiq.core.services.rules.changes import ChangeRules
from papiq.core.services.rules.running import Prepared, RuleRun

log = logging.getLogger(__name__)

MAX_PAGE = 200


@dataclass(frozen=True)
class MetadataChange:
    """A changed document; the caller's access afterwards (None if the rules filed it where
    the caller cannot see it); its space; the rules' run, for the owner and admins; the state
    before the change."""

    document: Document
    access: ShareLevel | None
    space: Space
    rules: RuleRun | None = None
    before: Document | None = None


@dataclass(frozen=True)
class DocumentView:
    """A document with the caller's access to it."""

    document: Document
    access: ShareLevel


@dataclass(frozen=True, kw_only=True)
class CodeFacts:
    """A code on the document's pages, once for its format and text however often it is
    printed: where it stands (page by page, a box per copy), a GiroCode read as its credit
    transfer, the identifiers it holds, and the amount fields of the document with the
    GiroCode's amount."""

    format: str
    text: str
    places: tuple[Location, ...]
    giro: GiroCode | None
    identifiers: tuple[tuple[IdentifierKind, str], ...]
    fields: tuple[FieldId, ...]


@dataclass(frozen=True, kw_only=True)
class DocumentFacts:
    """What the document shows of its contact (`domain.facts`, with spans of the text), the
    codes on its pages (None while they are not read), and, for admins, on the confirmed
    documents of how many contacts besides the document's an identifier stands."""

    contact_id: ContactId | None
    facts: Facts
    codes: tuple[CodeFacts, ...] | None
    other_contacts: dict[tuple[IdentifierKind, str], int] | None


async def _position(
    uow: UnitOfWork, order: DocumentOrder, after: DocumentId | None
) -> DocumentPosition | None:
    """Where a page ended: by the last document's id, and its value of the sort key."""
    if after is None:
        return None
    if order.key is SortKey.CREATED:
        return DocumentPosition(after)
    last = await uow.documents.find(after)
    if last is None:
        raise ValidationError("cursor: its document is gone; start from the first page")
    return DocumentPosition.of(last, order)


def _code_facts(codes: Sequence[Code], amounts: dict[FieldId, FieldValue]) -> tuple[CodeFacts, ...]:
    """The codes once for each format and text, in the order they first stand."""
    copies: dict[tuple[str, str], list[Code]] = {}
    for code in codes:
        copies.setdefault((code.format, code.text), []).append(code)
    return tuple(_one_code(found, amounts) for found in copies.values())


def _one_code(copies: Sequence[Code], amounts: dict[FieldId, FieldValue]) -> CodeFacts:
    code = copies[0]
    boxes: dict[int, list[Box]] = {}
    for item in copies:
        boxes.setdefault(item.page, []).append(item.box)
    giro = giro_code(code.text)
    fields: tuple[FieldId, ...] = ()
    if giro is not None and giro.amount is not None:
        fields = tuple(field for field, value in amounts.items() if value == giro.amount)
    return CodeFacts(
        format=code.format,
        text=code.text,
        places=tuple(Location(page, tuple(found)) for page, found in sorted(boxes.items())),
        giro=giro,
        identifiers=tuple(sorted(code_identifiers(code), key=lambda key: (key[0].value, key[1]))),
        fields=fields,
    )


@dataclass(frozen=True, kw_only=True)
class StoredFile:
    """A file of a document as it is stored: its size in bytes, its SHA-256, and what it says
    about itself (None if it cannot be read for that)."""

    size: int
    sha256: str
    metadata: FileMetadata | None


@dataclass(frozen=True, kw_only=True)
class DocumentFiles:
    """A document's files: the original, and the archive once there is one."""

    document: Document
    original: StoredFile
    archive: StoredFile | None


@dataclass(frozen=True)
class DocumentText:
    """A piece of the text read from the archive: `offset` and lengths count characters."""

    text: str
    offset: int
    total_length: int
    next_offset: int | None  # where the next piece starts; None at the end


class DocumentFile(StrEnum):
    ORIGINAL = "original"
    ARCHIVE = "archive"
    PREVIEW = "preview"
    PAGE = "page"  # the image of one page; `download` takes its number


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
        metadata: MetadataReader | None = None,
    ) -> None:
        """`rules`: run the change rules when a person changes a document's metadata;
        `metadata` reads what its files say about themselves."""
        self._uow = uow
        self._clock = clock
        self._store = object_store
        self._rules = rules
        self._metadata = metadata

    async def get(self, actor: UserId, id: DocumentId) -> Document:
        async with self._uow() as uow:
            document, _ = await readable_document(uow, await load_actor(uow, actor), id)
            return document

    async def view(self, actor: UserId, id: DocumentId) -> DocumentView:
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, space = await readable_document(uow, user, id)
            access = document_access(user, document, space)
        assert access is not None  # readable
        return DocumentView(document, access)

    async def read_text(
        self, actor: UserId, id: DocumentId, *, offset: int = 0, limit: int
    ) -> DocumentText:
        """`limit` characters of the document's text from `offset`. NotFoundError if the
        caller may not read the document; ValidationError for a negative offset or a limit
        below 1; ConflictError while the document has no text yet (it is not parsed)."""
        if offset < 0 or limit < 1:
            raise ValidationError("offset must be 0 or more and limit 1 or more")
        assert self._store is not None, "reading text needs the object store"
        async with self._uow() as uow:
            await readable_document(uow, await load_actor(uow, actor), id)
        try:
            data = await load_text(self._store, id)
        except NotFoundError:
            raise ConflictError("document has no text yet") from None
        text = data.decode("utf-8", errors="replace")
        end = offset + limit
        return DocumentText(
            text=text[offset:end],
            offset=offset,
            total_length=len(text),
            next_offset=end if end < len(text) else None,
        )

    async def facts(self, actor: UserId, id: DocumentId) -> DocumentFacts:
        """The facts of the document's text and codes, as the facts signal finds them now:
        a contact named or an identifier attached since counts at once. NotFoundError if the
        caller may not read the document; ConflictError while it has no text yet."""
        assert self._store is not None, "reading facts needs the object store"
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            contacts = await uow.contacts.list_all()
            definitions = {item.id: item for item in await uow.fields.list_all()}
        try:
            text = (await load_text(self._store, id)).decode("utf-8", errors="replace")
        except NotFoundError:
            raise ConflictError("document has no text yet") from None
        codes = await self._codes(id)
        facts = facts_in(text, contacts, codes or (), places=True)
        amounts = {
            field: value
            for field, value in document.fields.items()
            if field in definitions and definitions[field].data_type is FieldType.AMOUNT
        }
        views = None
        if codes is not None:
            views = _code_facts(codes, amounts)
        others = None
        if can_manage_master_data(user):
            async with self._uow() as uow:
                carrying = await uow.document_identifiers.contacts_of(
                    [item.key for item in facts.identifiers]
                )
            others = {
                key: len(contacts_ - {document.contact_id} if document.contact_id else contacts_)
                for key, contacts_ in carrying.items()
            }
        return DocumentFacts(
            contact_id=document.contact_id, facts=facts, codes=views, other_contacts=others
        )

    async def _codes(self, id: DocumentId) -> tuple[Code, ...] | None:
        """The codes read from the archive's pages, None while they are not (or of an earlier
        version)."""
        assert self._store is not None
        try:
            return codes_from_json(json.loads(await self._store.get(codes_key(id))))
        except NotFoundError:
            return None
        except (ValueError, ValidationError):
            log.warning("an unreadable file of codes", extra={"document_id": str(id)})
            return None

    async def read_structure(self, actor: UserId, id: DocumentId) -> bytes:
        """The parsed structure in the parser's JSON format (Docling's, with page and box of
        every element). NotFoundError if the caller may not read the document; ConflictError
        while it is not parsed."""
        assert self._store is not None, "reading the structure needs the object store"
        async with self._uow() as uow:
            await readable_document(uow, await load_actor(uow, actor), id)
        try:
            return await self._store.get(structure_key(id))
        except NotFoundError:
            raise ConflictError("document has no structure yet") from None

    async def query(
        self,
        actor: UserId,
        filter: DocumentFilter,
        *,
        order: DocumentOrder = NEWEST_FIRST,
        after: DocumentId | None = None,
        limit: int = 50,
        all_users: bool = False,
    ) -> list[DocumentView]:
        """Documents within the caller's reach matching `filter`, in `order` (newest first),
        after the page ending at the document `after`; with `all_users` (admins only), every
        document. Each is checked against the permission rules once more. ValidationError if
        `after` is gone while the list sorts by another key than the order of arrival."""
        limit = max(1, min(limit, MAX_PAGE))
        async with self._uow() as uow:
            user = await _lister(uow, actor, all_users)
            position = await _position(uow, order, after)
            filter = await expanded(uow, filter)
            if all_users:
                documents = await uow.documents.query(
                    filter, order=order, after=position, limit=limit
                )
            else:
                documents = await uow.documents.query_visible(
                    actor, filter, order=order, after=position, limit=limit
                )
            spaces: dict[SpaceId, Space] = {}
            views = []
            for document in documents:
                if document.space_id not in spaces:
                    spaces[document.space_id] = await uow.spaces.get(document.space_id)
                access = document_access(user, document, spaces[document.space_id])
                if access is None:
                    log.error(
                        "the repository listed a document the user may not read",
                        extra={"document_id": str(document.id), "user_id": str(actor)},
                    )
                    continue
                views.append(DocumentView(document, access))
        return views

    async def download(
        self, actor: UserId, id: DocumentId, file: DocumentFile, target: Path, *, page: int = 1
    ) -> FileInfo:
        """Write a file of a readable document to the local path `target`. NotFoundError if
        the caller may not read the document or the file does not exist (yet); for a page,
        also if the document has fewer pages."""
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
                key, info = preview_key(id), FileInfo(PAGE_MEDIA_TYPE, f"{stem}.webp")
            case DocumentFile.PAGE:
                if document.page_count is None or not 1 <= page <= document.page_count:
                    raise NotFoundError(f"page {page} of document", id)
                key, info = page_key(id, page), FileInfo(PAGE_MEDIA_TYPE, f"{stem}-{page}.webp")
        if not await self._store.exists(key):
            raise NotFoundError(f"{file.value} of document", id)
        await self._store.download(key, target)
        return info

    async def files(self, actor: UserId, id: DocumentId) -> DocumentFiles:
        """The files of a readable document as they are stored: size, SHA-256 and their
        metadata (a PDF's document information and XMP, an image's EXIF). NotFoundError if
        the caller may not read the document."""
        assert self._store is not None, "reading files needs the object store"
        async with self._uow() as uow:
            document, _ = await readable_document(uow, await load_actor(uow, actor), id)
        original = await self._stored(original_key(document.sha256))
        archive = None
        if await self._store.exists(archive_key(id)):
            archive = await self._stored(archive_key(id))
        return DocumentFiles(document=document, original=original, archive=archive)

    async def _stored(self, key: str) -> StoredFile:
        assert self._store is not None
        data = await self._store.get(key)
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        metadata = None
        if self._metadata is not None:
            try:
                metadata = await self._metadata.read_metadata(data)
            except UnprocessableDocumentError as error:
                log.warning(
                    "a file's metadata cannot be read", extra={"key": key, "reason": str(error)}
                )
        return StoredFile(size=len(data), sha256=digest, metadata=metadata)

    async def document_counts(
        self, actor: UserId, by: CountBy, *, all_users: bool = False
    ) -> dict[UUID, int]:
        """How many documents within the caller's reach (with `all_users`, admins only: every
        document) have each contact, type or tag, or a value for each field. A tag counts what
        its filter finds: the documents with it or a tag below it (each once), a group those
        whose contact or type is in it or a group below it (`_tags.tag_counts`)."""
        async with self._uow() as uow:
            await _lister(uow, actor, all_users)
            user = None if all_users else actor
            if by is CountBy.TAG:
                return await tag_counts(uow, user)
            return await uow.documents.count_by(by, user)

    async def list_visible(self, actor: UserId) -> list[Document]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.documents.list_visible_to(actor)

    async def filter_readers(self, id: DocumentId, candidates: Collection[UserId]) -> set[UserId]:
        """Those of `candidates` who may read the document now (admins: always); none if it does
        not exist. For pushing events to users (SSE), so it reads the current state."""
        if not candidates:
            return set()
        async with self._uow() as uow:
            document = await uow.documents.find(id)
            if document is None:
                return set()
            space = await uow.spaces.get(document.space_id)
            readers: set[UserId] = set()
            for candidate in set(candidates):
                user = await uow.users.find(candidate)
                if user is not None and can_read_document(user, document, space):
                    readers.add(candidate)
            return readers

    async def active_admins(self, candidates: Collection[UserId]) -> set[UserId]:
        """Those of `candidates` who are active admins: they hear of every document event (SSE),
        also of a deletion, whose stored readers name only those who had it within reach, and of
        a training of the classifier."""
        if not candidates:
            return set()
        async with self._uow() as uow:
            admins: set[UserId] = set()
            for candidate in set(candidates):
                user = await uow.users.find(candidate)
                if user is not None and user.is_active_admin:
                    admins.add(candidate)
            return admins

    async def processing_log(self, actor: UserId, id: DocumentId) -> list[NodeRun]:
        """Owner or admin: the log may hold technical details of failed runs."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not can_control_document(user, document):
                raise PermissionDeniedError(f"only the owner reads the processing log of {id}")
            return await uow.processing_log.list_for(id)

    async def exchanges(self, actor: UserId, id: DocumentId, node: Node) -> list[RunExchange]:
        """Owner or admin, as the log: what `node` sent to the language model and got back,
        every run, oldest first; none if it never asked one. ValidationError for a node that
        does not ask the model."""
        if node not in EXCHANGE_NODES:
            raise ValidationError(f"node {node.value} does not ask a language model")
        assert self._store is not None, "reading model exchanges needs the object store"
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not can_control_document(user, document):
                raise PermissionDeniedError(f"only the owner reads the model exchanges of {id}")
        for key in (exchanges_key(id, node), legacy_exchanges_key(id, node)):
            if key is None:
                continue
            try:
                data = await self._store.get(key)
            except NotFoundError:
                continue
            return exchanges_from_json(json.loads(data))
        return []

    async def inbox(
        self,
        actor: UserId,
        *,
        before: DocumentId | None = None,
        limit: int = 50,
        all_users: bool = False,
    ) -> list[InboxItem]:
        """The caller's yellow and red documents, newest first, with what is open in them;
        with `all_users` (admins only), those of every user."""
        limit = max(1, min(limit, MAX_PAGE))
        waiting = DocumentFilter(lanes=frozenset({Lane.YELLOW, Lane.RED}))
        async with self._uow() as uow:
            await _lister(uow, actor, all_users)
            after = None if before is None else DocumentPosition(before)
            if all_users:
                documents = await uow.documents.query(waiting, after=after, limit=limit)
            else:
                documents = await uow.documents.query_visible(
                    actor, waiting, after=after, limit=limit
                )
            items = []
            for document in documents:
                if not all_users and document.owner_id != actor:
                    continue  # only owners see their yellow and red documents in their reach
                log = await uow.processing_log.list_for(document.id)
                items.append(InboxItem(document, open_nodes(document, log)))
        return items

    async def review(self, actor: UserId, id: DocumentId) -> Review:
        """Owner or admin: what is open, and what the model proposed and how it was checked."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not can_control_document(user, document):
                raise PermissionDeniedError(f"only the owner reviews document {id}")
            log = await uow.processing_log.list_for(id)
        return Review(document, open_nodes(document, log), node_reviews(log))

    async def update_metadata(
        self, actor: UserId, id: DocumentId, changes: DocumentChanges
    ) -> Document:
        """Needs write access. Referenced contact, type, topic, tags and fields must exist; the
        topic is one the contact enables, or an admin's choice enables it there
        (`_access.checked_changes`). A change of confirming metadata confirms the document,
        unless it only adds or removes tags set by hand (`documents.confirms`)."""
        return (await self.change_metadata(actor, id, changes)).document

    async def change_metadata(
        self, actor: UserId, id: DocumentId, changes: DocumentChanges, *, dry_run: bool = False
    ) -> MetadataChange:
        """As `update_metadata`, and the change rules of the document's owner and the global
        ones run (see `rules.changes`). The rules' report is for the owner and admins. With
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
            # What the person decided is the change as given; one the checks add (a topic the
            # new contact does not enable, cleared) is none of their decisions.
            given = changes
            changes = await checked_changes(uow, given, document, user)
            definitions = {item.id: item for item in await uow.fields.list_all()}
            now = self._clock.now()
            before = copy.deepcopy(document)
            changed = document.apply_changes(changes, definitions, now)
            if confirms(
                changed, before.tag_ids, document.tag_ids, await _manual(uow, before, document)
            ):
                document.mark_confirmed(now)
            run = None
            if rules is not None:
                run = await rules.after_change(
                    uow,
                    actor=user,
                    before=before,
                    document=document,
                    changes=given,
                    prepared=prepared,
                    definitions=definitions,
                    now=now,
                    log=not dry_run,
                )
            space = await uow.spaces.get(document.space_id)
            access = document_access(user, document, space)
            if not dry_run:
                await _save(uow, document)
                await uow.commit()
        report = can_control_document(user, document)
        return MetadataChange(document, access, space, run if report else None, before)

    async def move(self, actor: UserId, id: DocumentId, space: SpaceId) -> None:
        """The owner moves into a space they may write to; an admin moves any document into
        any space."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not can_control_document(user, document):
                raise PermissionDeniedError(f"only the owner moves document {id}")
            target = await visible_space(uow, user, space)
            if not can_move_document(user, document, target):
                raise SpaceWriteDeniedError(space, str(space))
            now = self._clock.now()
            document.move_to(target.id, now)
            if self._rules is not None:
                await uow.processing_log.append(
                    self._rules.space_chosen(document, actor=user, now=now)
                )
            await _save(uow, document)
            await uow.commit()

    async def delete(self, actor: UserId, id: DocumentId) -> None:
        """Owner or admin. Removes the metadata and queues the removal of its files
        (derivatives, and the original unless another document has the same file)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not can_control_document(user, document):
                raise PermissionDeniedError(f"only the owner deletes document {id}")
            document.delete(self._clock.now(), await _readers(uow, document))
            await uow.documents.remove(id)
            await uow.outbox.add(document.pull_events())
            await uow.jobs.enqueue(
                REMOVE_FILES_JOB,
                {
                    "document_id": str(id),
                    "sha256": document.sha256.hex,
                    "page_count": document.page_count,
                },
                run_at=self._clock.now(),
            )
            await uow.commit()


async def _lister(uow: UnitOfWork, actor: UserId, all_users: bool) -> User:
    user = await load_actor(uow, actor)
    if all_users and not user.is_active_admin:
        raise PermissionDeniedError("only admins list the documents of all users")
    return user


async def _readers(uow: UnitOfWork, document: Document) -> list[UserId]:
    """The users who have the document within their reach now (`in_reach`): its owner, and
    while it is green the owner of its space and the users the space is shared with (if their
    accounts are active). Admins hear of the deletion through their rights, not this list."""
    space = await uow.spaces.get(document.space_id)
    candidates = {document.owner_id, space.owner_id, *space.shares}
    readers = []
    for candidate in candidates:
        user = await uow.users.find(candidate)
        if user is not None and in_reach(user, document, space):
            readers.append(candidate)
    return sorted(readers)


async def _manual(uow: UnitOfWork, before: Document, after: Document) -> frozenset[TagId]:
    """The tags added or removed between `before` and `after` that are set by hand only."""
    manual = set()
    for id in before.tag_ids ^ after.tag_ids:
        tag = await uow.tags.find(id)
        if tag is not None and tag.assignment is Assignment.MANUAL:
            manual.add(id)
    return frozenset(manual)


async def _save(uow: UnitOfWork, document: Document) -> None:
    await uow.documents.update(document)
    await uow.outbox.add(document.pull_events())

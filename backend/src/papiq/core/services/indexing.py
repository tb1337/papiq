"""Keeping the search index in line with the repository and the object store.

The index is derived data and lags a little behind: a document event creates a job, and the job
reads the current state of the document from the database and the object store and writes it
into the index. A job is idempotent and may be repeated.

- Every event of a document (`received`, `step_completed`, `lane_changed`, `filed`, `updated`,
  `deleted`) leads to a `search.index` job, without a dedup key: a key would also count a job
  that is running and has read its state already, and lose the change that arrived since.
- A job that finds the document gone removes it from the index.
- Two jobs for one document may run at the same time, and the older state may be written last.
  So a job checks after writing that the document and the names it carries are still as it read
  them, and writes again if not (`_ROUNDS`). The state written last is then one that matched the
  database after the write.
- Vectors are computed from sections of the text (`PAPIQ_SEARCH_CHUNK_SIZE`, at most
  `PAPIQ_SEARCH_MAX_CHUNKS`) and only when the sections differ from those of the indexed
  vectors: a change of metadata costs no embedding.
- Failed jobs are repeated with a growing delay (about six hours in all). If only the embedding
  fails, the last attempt writes the document without vectors; the reconciliation adds them.
- `search.refresh` follows the renaming of a contact, document type or tag: it queues an index
  job for every document that carries it.
- `search.reconcile` (recurring) compares the index with the database, queues jobs for what
  is missing or stale and removes what no longer exists. `search.rebuild` builds a new index from
  scratch next to the active one and swaps it in without an outage of the search.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from papiq.core.domain.attributes import AttributeValue, Money, Url
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import (
    ConcurrencyError,
    EmbeddingsError,
    NotFoundError,
    PermissionDeniedError,
    SearchIndexError,
)
from papiq.core.domain.events import DocumentEvent, DomainEvent
from papiq.core.domain.ids import ContactId, DocumentId, DocumentTypeId, TagId, UserId
from papiq.core.domain.jobs import Job
from papiq.core.domain.search import EmbeddingStamp, IndexDocument, IndexState
from papiq.core.ports import (
    Clock,
    Embeddings,
    ObjectStore,
    SearchIndex,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor
from papiq.core.services.objects import markdown_key

log = logging.getLogger(__name__)

SUBSCRIBER = "search.index"
INDEX_JOB = "search.index"
"""Payload: `document_id`."""
REFRESH_JOB = "search.refresh"
"""Payload: `kind` (`contact`, `document_type` or `tag`) and `id` of the renamed item."""
RECONCILE_JOB = "search.reconcile"
REBUILD_JOB = "search.rebuild"

_ROUNDS = 3
_RETRY_DELAY = timedelta(seconds=30)
_RETRY_MAX_DELAY = timedelta(hours=1)
_ATTEMPTS = {INDEX_JOB: 10, REFRESH_JOB: 10, REBUILD_JOB: 3}
_QUEUE_BATCH = 200


@dataclass(frozen=True)
class IndexingPolicy:
    max_text: int = 200_000  # characters of text per document in the index
    chunk_size: int = 1500  # characters per section that gets a vector
    max_chunks: int = 8  # sections per document; 1: one vector per document
    document_prefix: str = ""  # put before each section, for models that ask for it
    reconcile_interval: timedelta = timedelta(hours=6)
    job_lease: timedelta = timedelta(minutes=10)
    rebuild_lease: timedelta = timedelta(hours=6)
    batch_size: int = 50  # documents per request in a rebuild

    def __post_init__(self) -> None:
        if min(self.max_text, self.chunk_size, self.max_chunks, self.batch_size) < 1:
            raise ValueError("limits must be at least 1")


@dataclass(frozen=True)
class Rebuilt:
    documents: int
    queued: int  # documents the reconciliation afterwards queued again
    removed: int


@dataclass(frozen=True)
class Reconciled:
    queued: int  # missing or stale documents, queued for indexing
    removed: int  # documents in the index that no longer exist


@dataclass(frozen=True)
class _Snapshot:
    """A document with the names it is indexed with."""

    document: Document
    contact: str | None
    document_type: str | None
    tags: tuple[tuple[TagId, str], ...]

    @property
    def key(self) -> tuple[object, ...]:
        """What the index document depends on besides the text."""
        return (self.document.version, self.contact, self.document_type, self.tags)


class IndexingService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        object_store: ObjectStore,
        index: SearchIndex,
        embeddings: Embeddings | None = None,
        policy: IndexingPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._store = object_store
        self._index = index
        self._embeddings = embeddings
        self._policy = policy or IndexingPolicy()

    # --- events and jobs ------------------------------------------------------------------------

    async def on_event(self, event: DomainEvent) -> None:
        """The event bus subscriber: queue an index job for the document of the event."""
        if not isinstance(event, DocumentEvent):
            return
        async with self._uow() as uow:
            await uow.jobs.enqueue(
                INDEX_JOB, {"document_id": str(event.document_id)}, run_at=self._clock.now()
            )
            await uow.commit()

    async def schedule(self) -> None:
        """Make sure a reconciliation is queued; due at once unless one is queued already."""
        async with self._uow() as uow:
            await uow.jobs.enqueue(
                RECONCILE_JOB, {}, run_at=self._clock.now(), dedup_key=RECONCILE_JOB
            )
            await uow.commit()

    async def request_rebuild(self, actor: UserId) -> None:
        """Admins only: queue a rebuild of the index, unless one is queued or running."""
        async with self._uow() as uow:
            if not (await load_actor(uow, actor)).is_active_admin:
                raise PermissionDeniedError("only admins rebuild the search index")
            await uow.jobs.enqueue(REBUILD_JOB, {}, run_at=self._clock.now(), dedup_key=REBUILD_JOB)
            await uow.commit()

    async def run_next_job(self) -> bool:
        """Claim and run a due job of this service. Returns False if none was due. The rebuild
        has a lease of its own, as long as a rebuild may take."""
        for kinds, lease in (
            ([INDEX_JOB, REFRESH_JOB, RECONCILE_JOB], self._policy.job_lease),
            ([REBUILD_JOB], self._policy.rebuild_lease),
        ):
            async with self._uow() as uow:
                job = await uow.jobs.claim(now=self._clock.now(), lease=lease, kinds=kinds)
                await uow.commit()
            if job is not None:
                try:
                    await self._run_claimed(job)
                except asyncio.CancelledError:
                    await asyncio.shield(self._release(job))
                    raise
                return True
        return False

    async def _run_claimed(self, job: Job) -> None:
        try:
            work = self._work(job)
        except (KeyError, TypeError, ValueError) as error:
            log.error("invalid search job", extra={"job_id": str(job.id), "error": str(error)})
            await self._finish(job, error=f"invalid payload: {error}")
            return
        try:
            await work()
        except ConcurrencyError:
            log.warning("lost the claim of a search job", extra={"job_id": str(job.id)})
            return
        except Exception as error:
            await self._failed(job, error)
            return
        if job.kind == RECONCILE_JOB:
            await self._finish(job, next_reconcile=True)
        else:
            await self._finish(job)

    def _work(self, job: Job) -> Callable[[], Awaitable[object]]:
        match job.kind:
            case "search.index":
                document_id = DocumentId(UUID(str(job.payload["document_id"])))
                final = job.tries >= _ATTEMPTS[INDEX_JOB]
                return lambda: self.index_document(document_id, tolerate_embedding_failure=final)
            case "search.refresh":
                kind, item = str(job.payload["kind"]), UUID(str(job.payload["id"]))
                if kind not in _REFRESH_FIELDS:
                    raise ValueError(f"unknown kind {kind!r}")
                return lambda: self._refresh(kind, item)
            case "search.reconcile":
                return self.reconcile
            case _:
                return self.rebuild

    async def _failed(self, job: Job, error: Exception) -> None:
        reason = f"{type(error).__name__}: {error}"
        attempts = _ATTEMPTS.get(job.kind)
        log.warning(
            "search job failed", extra={"job_id": str(job.id), "kind": job.kind}, exc_info=True
        )
        try:
            async with self._uow() as uow:
                if job.kind == RECONCILE_JOB:
                    # Try again at the next interval, like the cleanup.
                    run_at = self._clock.now() + self._policy.reconcile_interval
                    await uow.jobs.reschedule(job, run_at=run_at, error=reason)
                elif attempts is not None and job.tries >= attempts:
                    log.error("search job given up", extra={"job_id": str(job.id), "error": reason})
                    await uow.jobs.fail(job, error=reason)
                else:
                    delay = min(_RETRY_DELAY * (1 << min(job.tries - 1, 20)), _RETRY_MAX_DELAY)
                    await uow.jobs.reschedule(job, run_at=self._clock.now() + delay, error=reason)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a search job", extra={"job_id": str(job.id)})

    async def _finish(
        self, job: Job, *, error: str | None = None, next_reconcile: bool = False
    ) -> None:
        try:
            async with self._uow() as uow:
                if error is None:
                    await uow.jobs.complete(job)
                else:
                    await uow.jobs.fail(job, error=error)
                if next_reconcile:
                    run_at = self._clock.now() + self._policy.reconcile_interval
                    await uow.jobs.enqueue(
                        RECONCILE_JOB, {}, run_at=run_at, dedup_key=RECONCILE_JOB
                    )
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a search job", extra={"job_id": str(job.id)})

    async def _release(self, job: Job) -> None:
        """The worker stops: give the job back to run again at once."""
        try:
            async with self._uow() as uow:
                await uow.jobs.release(job, run_at=self._clock.now(), error="worker stopped")
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a search job", extra={"job_id": str(job.id)})

    # --- one document ---------------------------------------------------------------------------

    async def index_document(
        self, id: DocumentId, *, tolerate_embedding_failure: bool = False
    ) -> None:
        """Bring the index entry of a document to its current state; remove it if the document
        is gone. `tolerate_embedding_failure`: if the embedding fails, write without vectors."""
        for _ in range(_ROUNDS):
            snapshot = await self._snapshot(id)
            if snapshot is None:
                await self._index.remove(id)
                return
            text = await self._read_text(id)
            state = await self._index.state(id)
            try:
                entry = await self._compose(snapshot, text, state)
            except EmbeddingsError as error:
                if not tolerate_embedding_failure:
                    raise
                log.warning(
                    "indexing without vectors, the embedding failed",
                    extra={"document_id": str(id), "error": str(error)},
                )
                entry = await self._compose(snapshot, text, state, embed=False)
            await self._index.upsert([entry])
            after = await self._snapshot(id)
            if after is not None and after.key == snapshot.key:
                return
            if after is None:
                await self._index.remove(id)
                return
        raise SearchIndexError(f"document {id} kept changing while it was indexed")

    async def _snapshot(self, id: DocumentId) -> _Snapshot | None:
        async with self._uow() as uow:
            document = await uow.documents.find(id)
            if document is None:
                return None
            return await _snapshot_of(uow, document)

    async def _read_text(self, id: DocumentId) -> str:
        try:
            data = await self._store.get(markdown_key(id))
        except NotFoundError:
            return ""  # not parsed yet
        return data.decode("utf-8", errors="replace")[: self._policy.max_text]

    async def _compose(
        self, snapshot: _Snapshot, text: str, state: IndexState | None, *, embed: bool = True
    ) -> IndexDocument:
        """The index entry; `state` is what the index holds now (None: build for a new index)."""
        document = snapshot.document
        vectors: tuple[tuple[float, ...], ...] | None = ()
        stamp: EmbeddingStamp | None = None
        if embed and self._embeddings is not None:
            sections = self._sections(snapshot, text)
            stamp = EmbeddingStamp(
                self._embeddings.model, _digest(self._embeddings.model, sections)
            )
            if state is not None and state.embedding == stamp:
                vectors, stamp = None, None  # the text did not change: keep the vectors
            else:
                result = await self._embeddings.embed(sections)
                if len(result.vectors) != len(sections):
                    raise EmbeddingsError(f"expected {len(sections)} vectors")
                vectors = tuple(tuple(vector) for vector in result.vectors)
        return IndexDocument(
            id=document.id,
            version=document.version,
            owner_id=document.owner_id,
            drawer_id=document.drawer_id,
            lane=document.lane,
            title=document.title,
            filename=document.original_filename,
            text=text,
            contact_id=document.contact_id,
            contact=snapshot.contact,
            document_type_id=document.document_type_id,
            document_type=snapshot.document_type,
            tag_ids=tuple(tag for tag, _ in snapshot.tags),
            tags=tuple(name for _, name in snapshot.tags),
            attributes=_attribute_texts(document),
            document_date=document.document_date,
            created_at=document.created_at,
            vectors=vectors,
            embedding=stamp,
        )

    def _sections(self, snapshot: _Snapshot, text: str) -> list[str]:
        """The texts that get a vector: the text in sections, the first one headed by what the
        document says about itself."""
        header = "\n".join(
            part
            for part in (
                snapshot.document.title,
                snapshot.contact,
                snapshot.document_type,
                ", ".join(name for _, name in snapshot.tags),
            )
            if part
        )
        sections = split_sections(text, self._policy.chunk_size, self._policy.max_chunks)
        sections[0:1] = [f"{header}\n\n{sections[0]}" if sections else header]
        prefix = self._policy.document_prefix
        return [prefix + section for section in sections]

    # --- master data renamed --------------------------------------------------------------------

    async def _refresh(self, kind: str, item: UUID) -> None:
        """Queue an index job for every document with the contact, type or tag `item`."""
        async with self._uow() as uow:
            documents = await uow.documents.list_all()
        carrying = [document.id for document in documents if _carries(document, kind, item)]
        await self._queue(carrying)

    async def _queue(self, documents: list[DocumentId]) -> None:
        for start in range(0, len(documents), _QUEUE_BATCH):
            async with self._uow() as uow:
                for id in documents[start : start + _QUEUE_BATCH]:
                    await uow.jobs.enqueue(
                        INDEX_JOB, {"document_id": str(id)}, run_at=self._clock.now()
                    )
                await uow.commit()

    # --- the whole index ------------------------------------------------------------------------

    async def reconcile(self) -> Reconciled:
        """Queue index jobs for documents that are missing or stale in the index and remove
        entries of documents that are gone. A stale document has another version than the
        database, or (with embeddings) vectors of no or another model."""
        indexed = {state.id: state async for state in self._index.states()}
        async with self._uow() as uow:
            documents = await uow.documents.list_all()
        model = None if self._embeddings is None else self._embeddings.model
        stale: list[DocumentId] = []
        for document in documents:
            state = indexed.pop(document.id, None)
            if (
                state is None
                or state.version != document.version
                or (
                    model is not None
                    and (state.embedding is None or state.embedding.model != model)
                )
            ):
                stale.append(document.id)
        for id in indexed:  # not in the database
            await self._index.remove(id)
        await self._queue(stale)
        log.info("search index reconciled", extra={"queued": len(stale), "removed": len(indexed)})
        return Reconciled(queued=len(stale), removed=len(indexed))

    async def rebuild(self, progress: Callable[[int, int], None] | None = None) -> Rebuilt:
        """Build a new index from the database and the object store while the active one keeps
        serving, swap it in, and reconcile what changed meanwhile. `progress(done, total)`."""
        async with self._uow() as uow:
            ids = sorted(document.id for document in await uow.documents.list_all())
        build = await self._index.begin_rebuild()
        try:
            added = 0
            for start in range(0, len(ids), self._policy.batch_size):
                entries = []
                for id in ids[start : start + self._policy.batch_size]:
                    snapshot = await self._snapshot(id)
                    if snapshot is not None:  # deleted meanwhile
                        entries.append(
                            await self._compose(snapshot, await self._read_text(id), None)
                        )
                await build.add(entries)
                added += len(entries)
                if progress is not None:
                    progress(start + len(ids[start : start + self._policy.batch_size]), len(ids))
            await build.finish()
        except BaseException:
            await asyncio.shield(build.abort())
            raise
        # Jobs that ran during the rebuild wrote to the old index.
        reconciled = await self.reconcile()
        log.info("search index rebuilt", extra={"documents": added})
        return Rebuilt(documents=added, queued=reconciled.queued, removed=reconciled.removed)


_REFRESH_FIELDS = {"contact", "document_type", "tag"}


def _carries(document: Document, kind: str, item: UUID) -> bool:
    match kind:
        case "contact":
            return document.contact_id == ContactId(item)
        case "document_type":
            return document.document_type_id == DocumentTypeId(item)
        case _:
            return TagId(item) in document.tag_ids


async def _snapshot_of(uow: UnitOfWork, document: Document) -> _Snapshot:
    contact = None
    if document.contact_id is not None:
        found = await uow.contacts.find(document.contact_id)
        contact = None if found is None else found.name
    document_type = None
    if document.document_type_id is not None:
        kind = await uow.document_types.find(document.document_type_id)
        document_type = None if kind is None else kind.name
    tags = []
    for tag_id in sorted(document.tag_ids, key=str):
        tag = await uow.tags.find(tag_id)
        if tag is not None:
            tags.append((tag.id, tag.name))
    return _Snapshot(document, contact, document_type, tuple(tags))


def _digest(model: str, sections: list[str]) -> str:
    return hashlib.sha256("\0".join([model, *sections]).encode()).hexdigest()[:16]


def _attribute_texts(document: Document) -> tuple[str, ...]:
    texts = []
    for value in document.attributes.values():
        text = _attribute_text(value)
        if text:
            texts.append(text)
    return tuple(texts)


def _attribute_text(value: AttributeValue) -> str:
    """A value as the words a person would search for. Yes/no values say nothing without
    their attribute's name."""
    match value:
        case bool():
            return ""
        case Money():
            return f"{value.amount:f} {value.currency}"
        case Decimal():
            return f"{value:f}"
        case Url():
            return value.value
        case str():
            return value
        case _:  # a date, in both notations
            return f"{value.isoformat()} {value:%d.%m.%Y}"


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def split_sections(text: str, size: int, limit: int) -> list[str]:
    """The text in at most `limit` sections of about `size` characters, cut at paragraph
    breaks (or at a space, inside a paragraph longer than `size`)."""
    sections: list[str] = []
    current = ""
    for paragraph in (part.strip() for part in _PARAGRAPH_BREAK.split(text)):
        if not paragraph:
            continue
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", size // 2, size)
            cut = size if cut == -1 else cut
            if current:
                sections.append(current)
                current = ""
            sections.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + 2 + len(paragraph) > size:
            sections.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
        if len(sections) >= limit:
            break
    if current and len(sections) < limit:
        sections.append(current)
    return [section for section in sections[:limit] if section]

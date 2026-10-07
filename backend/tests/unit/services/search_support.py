"""Shared by the tests of the indexing and the search service: a world with a search index that
the event bus and the jobs keep up to date."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from papiq.adapters.outbound.memory import FakeEmbeddings, MemoryEventBus, MemorySearchIndex
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import EmbeddingsError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.jobs import Job
from papiq.core.domain.search import IndexDocument
from papiq.core.domain.users import User
from papiq.core.ports import EmbeddingResult, IndexBuild
from papiq.core.services.indexing import INDEX_JOB, SUBSCRIBER, IndexingPolicy, IndexingService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.objects import markdown_key
from tests.builders import incoming
from tests.unit.services.conftest import World

HOUR = timedelta(hours=1)


class BrokenEmbeddings:
    """An embedding service that is down until `up` is set."""

    model = "fake-embeddings 1"

    def __init__(self) -> None:
        self.up = False
        self.working = FakeEmbeddings(model=self.model)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not self.up:
            raise EmbeddingsError("connection refused")
        return await self.working.embed(texts)


class RacingIndex(MemorySearchIndex):
    """Runs `before_write` once, just before the first write: another job is at work now."""

    def __init__(self) -> None:
        super().__init__()
        self.before_write: Callable[[], Awaitable[object]] | None = None

    async def upsert(self, documents: Sequence[IndexDocument]) -> None:
        hook, self.before_write = self.before_write, None
        if hook is not None:
            await hook()
        await super().upsert(documents)


class _HookedBuild:
    def __init__(self, build: IndexBuild, hook: Callable[[], Awaitable[object]] | None) -> None:
        self._build, self._hook = build, hook

    async def add(self, documents: Sequence[IndexDocument]) -> None:
        await self._build.add(documents)
        hook, self._hook = self._hook, None
        if hook is not None:
            await hook()

    async def finish(self) -> None:
        await self._build.finish()

    async def abort(self) -> None:
        await self._build.abort()


class HookedIndex(MemorySearchIndex):
    """Runs `after_first_batch` once the first batch of a rebuild is written."""

    after_first_batch: Callable[[], Awaitable[object]] | None = None

    async def begin_rebuild(self) -> IndexBuild:
        return _HookedBuild(await super().begin_rebuild(), self.after_first_batch)


@dataclass
class Setup:
    world: World
    index: MemorySearchIndex = field(default_factory=MemorySearchIndex)
    embeddings: FakeEmbeddings | BrokenEmbeddings | None = field(default_factory=FakeEmbeddings)
    policy: IndexingPolicy = field(default_factory=IndexingPolicy)

    def __post_init__(self) -> None:
        self.bus = MemoryEventBus(self.world.database, clock=self.world.clock)
        self.bus.subscribe(SUBSCRIBER, self.indexing.on_event)

    @property
    def indexing(self) -> IndexingService:
        return IndexingService(
            self.world.uow,
            self.world.clock,
            self.world.object_store,
            self.index,
            self.embeddings,
            self.policy,
        )

    @property
    def master_data(self) -> MasterDataService:
        return MasterDataService(self.world.uow, self.world.clock, index_renames=True)

    async def settle(self, *, pipeline: bool = True, pipeline_service: object = None) -> None:
        """Deliver events and run jobs until nothing is left to do."""
        indexing = self.indexing
        while True:
            moved = await self.bus.dispatch()
            if pipeline:
                moved += await self.world.drain(pipeline_service)  # type: ignore[arg-type]
            while await indexing.run_next_job():
                moved += 1
            if not moved:
                return

    async def document(self, owner: User, text: str = "Stromrechnung September") -> Document:
        """A document that went through the pipeline (green), with `text` as its parsed text."""
        received = await self.world.pipeline().receive(
            owner.id, incoming(b"%PDF-1.7 " + text.encode()), filename="strom.pdf"
        )
        await self.put_text(received.id, text)
        await self.settle()
        return await self.world.documents.get(owner.id, received.id)

    async def put_text(self, id: DocumentId, text: str) -> None:
        await self.world.object_store.put(
            markdown_key(id), text.encode(), content_type="text/plain"
        )

    def entry(self, id: DocumentId) -> IndexDocument:
        return self.index.documents[id]


async def run_due(indexing: IndexingService) -> int:
    count = 0
    while await indexing.run_next_job():
        count += 1
    return count


def index_jobs(world: World) -> list[Job]:
    return [job for job in world.database.jobs.values() if job.kind == INDEX_JOB]

"""Searching: what the caller may read, checked in the index and again in the database."""

import asyncio
from collections.abc import Sequence
from datetime import timedelta

import pytest

from papiq.adapters.outbound.memory import FakeEmbeddings
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import (
    AuthenticationError,
    PermissionDeniedError,
    SearchUnavailableError,
    ValidationError,
)
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.pipeline import Step
from papiq.core.domain.users import Role, User
from papiq.core.ports import (
    DocumentFilter,
    EmbeddingResult,
    Embeddings,
    SearchQuery,
    SearchResult,
)
from papiq.core.services.search import SearchPolicy, SearchService
from tests.builders import UNCERTAIN
from tests.unit.services.conftest import Returns, World
from tests.unit.services.search_support import BrokenEmbeddings, Setup


class Scene:
    setup: Setup
    owner: User
    reader: User
    stranger: User
    shared: Drawer
    shared_document: Document
    private_document: Document

    def search(
        self, policy: SearchPolicy | None = None, embeddings: Embeddings | str | None = "same"
    ) -> SearchService:
        used = self.setup.embeddings if isinstance(embeddings, str) else embeddings
        return SearchService(self.setup.world.uow, self.setup.index, used, policy)

    async def ids(
        self, user: User, text: str, filter: DocumentFilter | None = None
    ) -> set[DocumentId]:
        page = await self.search(embeddings=None).search(user.id, text, filter)
        return {item.document.id for item in page.items}


@pytest.fixture
async def scene(world: World) -> Scene:
    scene = Scene()
    scene.setup = Setup(world)
    scene.owner, scene.reader = await world.user("owner"), await world.user("reader")
    scene.stranger = await world.user("stranger")
    scene.shared = await world.drawers.create(scene.owner.id, "Household")
    await world.drawers.share(scene.owner.id, scene.shared.id, scene.reader.id, ShareLevel.READ)
    scene.shared_document = await scene.setup.document(scene.owner, "Stromrechnung September")
    await world.documents.move(scene.owner.id, scene.shared_document.id, scene.shared.id)
    scene.private_document = await scene.setup.document(scene.owner, "Mietvertrag Wohnung")
    await scene.setup.settle()
    return scene


async def test_the_owner_finds_everything_in_any_lane(scene: Scene) -> None:
    assert await scene.ids(scene.owner, "Stromrechnung") == {scene.shared_document.id}
    assert await scene.ids(scene.owner, "Mietvertrag") == {scene.private_document.id}
    await scene.setup.world.pipeline().reprocess_from(
        scene.owner.id, scene.private_document.id, Step.CLASSIFY
    )
    await scene.setup.settle(
        pipeline_service=scene.setup.world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)})
    )
    assert await scene.ids(scene.owner, "Mietvertrag") == {scene.private_document.id}


async def test_others_find_green_documents_of_shared_drawers_only(scene: Scene) -> None:
    assert await scene.ids(scene.reader, "Stromrechnung") == {scene.shared_document.id}
    assert await scene.ids(scene.reader, "Mietvertrag") == set()
    assert await scene.ids(scene.stranger, "Stromrechnung") == set()
    assert await scene.ids(scene.stranger, "Mietvertrag") == set()


async def test_meaning_finds_only_what_the_caller_may_read(scene: Scene) -> None:
    """Vectors rank every document of the index; the rights hold for them too."""
    for user, expected in (
        (scene.owner, {scene.shared_document.id, scene.private_document.id}),
        (scene.reader, {scene.shared_document.id}),
        (scene.stranger, set()),
    ):
        page = await scene.search().search(user.id, "Stromrechnung", semantic_ratio=1.0)
        assert page.semantic
        assert {item.document.id for item in page.items} == expected


async def test_a_yellow_document_is_not_found_by_others(scene: Scene) -> None:
    await scene.setup.world.pipeline().reprocess_from(
        scene.owner.id, scene.shared_document.id, Step.CLASSIFY
    )
    await scene.setup.settle(
        pipeline_service=scene.setup.world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)})
    )
    assert await scene.ids(scene.reader, "Stromrechnung") == set()
    assert await scene.ids(scene.owner, "Stromrechnung") == {scene.shared_document.id}


async def test_a_withdrawn_share_hides_the_documents_at_once(scene: Scene) -> None:
    await scene.setup.world.drawers.unshare(scene.owner.id, scene.shared.id, scene.reader.id)
    assert await scene.ids(scene.reader, "Stromrechnung") == set()


async def test_the_database_is_asked_again_when_the_index_is_behind(scene: Scene) -> None:
    world = scene.setup.world
    # Changes the index has not heard of yet: no event is delivered, no job runs.
    await world.pipeline().reprocess_from(scene.owner.id, scene.shared_document.id, Step.CLASSIFY)
    await world.drain(world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)}))
    state = await scene.setup.index.state(scene.shared_document.id)
    assert state is not None
    assert scene.setup.entry(scene.shared_document.id).lane_value == "green"  # the index is behind
    assert await scene.ids(scene.reader, "Stromrechnung") == set()

    await world.documents.move(scene.owner.id, scene.private_document.id, scene.shared.id)
    assert scene.setup.entry(scene.private_document.id).drawer_id != scene.shared.id
    assert await scene.ids(scene.reader, "Mietvertrag") == set()  # not in the index for reader


async def test_a_document_moved_out_of_a_shared_drawer_is_dropped(scene: Scene) -> None:
    world = scene.setup.world
    private = await world.drawers.create(scene.owner.id, "Private")
    await world.documents.move(scene.owner.id, scene.shared_document.id, private.id)
    assert scene.setup.entry(scene.shared_document.id).drawer_id == scene.shared.id  # still
    assert await scene.ids(scene.reader, "Stromrechnung") == set()


async def test_a_deleted_document_is_dropped(scene: Scene) -> None:
    await scene.setup.world.documents.delete(scene.owner.id, scene.shared_document.id)
    assert scene.shared_document.id in scene.setup.index.documents  # not removed yet
    assert await scene.ids(scene.owner, "Stromrechnung") == set()


async def test_the_filter_is_applied_to_the_database_state_too(scene: Scene) -> None:
    world = scene.setup.world
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Stadtwerke")
    await world.documents.update_metadata(
        scene.owner.id, scene.shared_document.id, DocumentChanges(contact_id=contact.id)
    )
    await scene.setup.settle()
    only = DocumentFilter(contact=contact.id)
    assert await scene.ids(scene.owner, "Stromrechnung", only) == {scene.shared_document.id}
    await world.documents.update_metadata(
        scene.owner.id, scene.shared_document.id, DocumentChanges(contact_id=None)
    )  # the index still says the contact
    assert await scene.ids(scene.owner, "Stromrechnung", only) == set()


async def test_a_deactivated_user_finds_nothing(scene: Scene) -> None:
    admin = await scene.setup.world.user(role=Role.ADMIN)
    await scene.setup.world.users.set_active(admin.id, scene.reader.id, False)
    with pytest.raises(AuthenticationError):
        await scene.ids(scene.reader, "Stromrechnung")


async def test_the_page_carries_access_score_and_snippet(scene: Scene) -> None:
    page = await scene.search().search(scene.reader.id, "Stromrechnung")
    (item,) = page.items
    assert item.access is ShareLevel.READ
    assert item.score is not None
    assert any(segment.match for segment in item.snippet)
    owner_page = await scene.search().search(scene.owner.id, "Stromrechnung")
    assert owner_page.items[0].access is ShareLevel.READ_WRITE


async def test_paging_goes_through_all_hits(scene: Scene) -> None:
    for number in range(4):
        await scene.setup.document(scene.owner, f"Rechnung {number} Telefon")
    seen: list[object] = []
    offset: int | None = 0
    pages = 0
    while offset is not None:
        page = await scene.search(embeddings=None).search(
            scene.owner.id, "Rechnung", offset=offset, limit=2
        )
        seen += [item.document.id for item in page.items]
        offset = page.next_offset
        pages += 1
    assert len(seen) == len(set(seen)) == 5  # four new ones and the Stromrechnung
    assert pages == 3


async def test_a_page_with_dropped_hits_still_leads_to_the_next(scene: Scene) -> None:
    world = scene.setup.world
    for number in range(3):
        await scene.setup.document(scene.owner, f"Rechnung {number}")
    first = await scene.search().search(scene.owner.id, "Rechnung", limit=1)
    await world.documents.delete(scene.owner.id, first.items[0].document.id)
    page = await scene.search().search(scene.owner.id, "Rechnung", limit=1)
    assert page.next_offset == 1  # the index still counts the deleted one
    assert page.items == [] or page.items[0].document.id != first.items[0].document.id


async def test_paging_stops_at_the_hits_an_index_can_reach(scene: Scene) -> None:
    far = await scene.search().search(scene.owner.id, "Stromrechnung", offset=1000)
    assert (far.items, far.next_offset) == ([], None)
    near = await scene.search().search(scene.owner.id, "Stromrechnung", offset=995, limit=20)
    assert near.next_offset is None


async def test_a_search_with_meaning_pages_on_whatever_the_words_estimate(scene: Scene) -> None:
    class Estimating(type(scene.setup.index)):  # type: ignore[misc]
        """An index whose estimate of the hits is the page it has just given."""

        async def search(self, query: SearchQuery) -> SearchResult:
            result = await super().search(query)
            return SearchResult(
                hits=result.hits, estimated_total=len(result.hits), semantic=result.semantic
            )

    index = Estimating()
    index.replace_all(dict(scene.setup.index.documents))
    service = SearchService(scene.setup.world.uow, index, scene.setup.embeddings)
    with_meaning = await service.search(scene.owner.id, "Rechnung", limit=1, semantic_ratio=0.5)
    assert (with_meaning.semantic, with_meaning.next_offset) == (True, 1)
    words = await service.search(scene.owner.id, "Rechnung", limit=1, semantic_ratio=0)
    assert (words.semantic, words.next_offset) == (False, None)


async def test_a_query_vector_of_another_length_goes_by_words(scene: Scene) -> None:
    service = scene.search(SearchPolicy(dimensions=3))  # the fake vectors have more
    page = await service.search(scene.owner.id, "Stromrechnung", semantic_ratio=0.5)
    assert page.semantic is False
    assert [item.document.id for item in page.items] == [scene.shared_document.id]


async def test_with_embeddings_the_meaning_takes_part(scene: Scene) -> None:
    embeddings = scene.setup.embeddings
    assert isinstance(embeddings, FakeEmbeddings)
    before = len(embeddings.calls)
    page = await scene.search().search(scene.owner.id, "Stromrechnung")
    assert page.semantic
    assert embeddings.calls[before:] == [["Stromrechnung"]]


async def test_the_query_gets_its_prefix(scene: Scene) -> None:
    embeddings = scene.setup.embeddings
    assert isinstance(embeddings, FakeEmbeddings)
    await scene.search(SearchPolicy(query_prefix="query: ")).search(scene.owner.id, "Strom")
    assert embeddings.calls[-1] == ["query: Strom"]


async def test_a_ratio_of_zero_embeds_nothing(scene: Scene) -> None:
    embeddings = scene.setup.embeddings
    assert isinstance(embeddings, FakeEmbeddings)
    before = len(embeddings.calls)
    page = await scene.search().search(scene.owner.id, "Strom", semantic_ratio=0)
    assert not page.semantic
    assert len(embeddings.calls) == before


async def test_without_embeddings_it_is_full_text(scene: Scene) -> None:
    page = await scene.search(embeddings=None).search(scene.owner.id, "Stromrechnung")
    assert not page.semantic
    assert [item.document.id for item in page.items] == [scene.shared_document.id]


async def test_a_down_embedding_service_leaves_full_text(scene: Scene) -> None:
    page = await scene.search(embeddings=BrokenEmbeddings()).search(scene.owner.id, "Stromrechnung")
    assert not page.semantic
    assert [item.document.id for item in page.items] == [scene.shared_document.id]


async def test_a_slow_embedding_service_leaves_full_text(scene: Scene) -> None:
    class Slow:
        model = "slow"

        async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
            await asyncio.sleep(5)
            raise AssertionError("too slow")

    policy = SearchPolicy(embed_timeout=timedelta(milliseconds=20))
    page = await scene.search(policy, Slow()).search(scene.owner.id, "Stromrechnung")
    assert not page.semantic
    assert len(page.items) == 1


async def test_an_unreachable_index_raises(scene: Scene) -> None:
    class Down:
        async def search(self, query: SearchQuery) -> SearchResult:
            raise SearchUnavailableError("connection refused")

    service = SearchService(scene.setup.world.uow, Down())  # type: ignore[arg-type]
    with pytest.raises(SearchUnavailableError):
        await service.search(scene.owner.id, "Strom")


async def test_the_query_is_checked(scene: Scene) -> None:
    service = scene.search()
    for options in (
        {"text": "  "},
        {"text": "x", "semantic_ratio": 1.5},
        {"text": "x", "limit": 0},
        {"text": "x", "limit": 101},
        {"text": "x", "offset": -1},
    ):
        with pytest.raises(ValidationError):
            await service.search(scene.owner.id, **options)


async def test_the_index_never_sees_a_query_without_the_callers_rights(scene: Scene) -> None:
    seen: list[SearchQuery] = []

    class Spy:
        async def search(self, query: SearchQuery) -> SearchResult:
            seen.append(query)
            return SearchResult(hits=[], estimated_total=0, semantic=False)

    service = SearchService(scene.setup.world.uow, Spy())  # type: ignore[arg-type]
    await service.search(scene.reader.id, "Strom")
    (query,) = seen
    assert query.visibility.user == scene.reader.id
    assert scene.shared.id in query.visibility.drawers
    assert len(query.visibility.drawers) == 2  # the shared drawer and the reader's own


def test_policy_ratio_is_checked() -> None:
    with pytest.raises(ValueError):
        SearchPolicy(semantic_ratio=2)


async def test_admins_search_their_reach_or_everything(scene: Scene) -> None:
    """Tobi, 08.10.2026: admins see everything; by default their search is their reach, as for
    every user, and `all_users` widens it to every document. Nobody else may ask for it."""
    admin = await scene.setup.world.user("admin", role=Role.ADMIN)
    search = scene.search(embeddings=None)
    assert (await search.search(admin.id, "Stromrechnung")).items == []
    for text, document in (
        ("Stromrechnung", scene.shared_document),
        ("Mietvertrag", scene.private_document),
    ):
        everything = await search.search(admin.id, text, all_users=True)
        assert [(item.document.id, item.access) for item in everything.items] == [
            (document.id, ShareLevel.READ_WRITE)
        ]
    for user in (scene.owner, scene.reader, scene.stranger):
        with pytest.raises(PermissionDeniedError):
            await search.search(user.id, "Stromrechnung", all_users=True)
    other = await scene.setup.world.user("other admin", role=Role.ADMIN)
    await scene.setup.world.users.set_active(other.id, admin.id, False)
    with pytest.raises(AuthenticationError):
        await search.search(admin.id, "Stromrechnung", all_users=True)

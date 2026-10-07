"""The Meilisearch adapter against a simulated Meilisearch: what it sends, how it reads the
answers, how it treats failures. No network; the real service is covered by the integration
tests."""

import json
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

import httpx2
import pytest

from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.adapters.outbound.meilisearch.index import (
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    filter_expression,
    segments,
)
from papiq.core.domain.errors import SearchIndexError, SearchUnavailableError
from papiq.core.domain.ids import ContactId, DocumentId, DocumentTypeId, DrawerId, TagId, UserId
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.search import EmbeddingStamp, Segment, Visibility
from papiq.core.ports import DocumentFilter, SearchQuery
from tests.builders import NOW, index_document

URL = "http://meilisearch:7700"


def uid(n: int) -> UUID:
    return UUID(int=n)


class SimulatedMeilisearch:
    """Answers like Meilisearch for the calls the adapter makes; records every request. The
    index exists with the wanted settings unless `settings` says otherwise."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.task_status = "succeeded"
        self.task_error: dict[str, Any] = {}
        self.pending_polls = 0
        self.index_exists = True
        self.deleting_a_missing_index = False  # the task of a DELETE /indexes/... fails
        self.settings: dict[str, Any] | None = None  # None: the index has what is wanted
        self.search_answer: dict[str, Any] = {"hits": [], "estimatedTotalHits": 0}
        self.fail: Callable[[httpx2.Request], httpx2.Response | None] = lambda _: None

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        override = self.fail(request)
        if override is not None:
            return override
        path, method = request.url.path, request.method
        if path == "/tasks/7":
            return httpx2.Response(
                200,
                json={
                    "uid": 7,
                    "status": "failed",
                    "error": {"code": "index_not_found", "message": "Index not found"},
                },
            )
        if (
            method == "DELETE"
            and path.count("/") == 2
            and path.startswith("/indexes/")
            and self.deleting_a_missing_index
        ):
            return httpx2.Response(202, json={"taskUid": 7, "status": "enqueued"})
        if path.startswith("/tasks/"):
            if self.pending_polls > 0:
                self.pending_polls -= 1
                return httpx2.Response(200, json={"uid": 1, "status": "processing"})
            return httpx2.Response(
                200, json={"uid": 1, "status": self.task_status, "error": self.task_error}
            )
        if path.endswith("/settings") and method == "GET":
            if not self.index_exists:
                return httpx2.Response(
                    404, json={"code": "index_not_found", "message": "Index not found"}
                )
            return httpx2.Response(200, json=self.settings or {})
        if path.endswith("/search"):
            return httpx2.Response(200, json=self.search_answer)
        if path == "/health":
            return httpx2.Response(200, json={"status": "available"})
        if path == "/version":
            return httpx2.Response(200, json={"pkgVersion": "1.54.3"})
        if path.endswith("/documents/fetch"):
            return httpx2.Response(200, json={"results": [], "total": 0})
        return httpx2.Response(202, json={"taskUid": 1, "status": "enqueued"})

    def index(self, **arguments: Any) -> MeilisearchIndex:
        return MeilisearchIndex(
            url=URL,
            transport=httpx2.MockTransport(self.handle),
            index="papiq-test",
            **arguments,
        )

    def calls(self, method: str, suffix: str = "") -> list[httpx2.Request]:
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path.endswith(suffix)
        ]

    def body(self, request: httpx2.Request) -> Any:
        return json.loads(request.content)


def wanted_settings(sim: SimulatedMeilisearch, **arguments: Any) -> dict[str, Any]:
    return sim.index(**arguments)._settings()


async def test_a_missing_index_is_created_with_its_settings() -> None:
    sim = SimulatedMeilisearch()
    sim.index_exists = False
    index = sim.index(dimensions=3, locales=["deu"])
    await index.upsert([index_document()])
    (create,) = sim.calls("POST", "/indexes")
    assert sim.body(create) == {"uid": "papiq-test", "primaryKey": "id"}
    (patch,) = sim.calls("PATCH", "/settings")
    settings = sim.body(patch)
    assert settings["localizedAttributes"] == [{"locales": ["deu"], "attributePatterns": ["*"]}]
    assert settings["embedders"] == {"default": {"source": "userProvided", "dimensions": 3}}
    assert settings["pagination"] == {"maxTotalHits": 1000}
    assert "owner_id" in settings["filterableAttributes"]
    assert settings["searchableAttributes"][0] == "title"


async def test_settings_that_are_in_place_are_not_applied_again() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=3)
    sim.settings["filterableAttributes"] = list(reversed(sim.settings["filterableAttributes"]))
    index = sim.index(dimensions=3)
    await index.upsert([index_document()])
    await index.upsert([index_document()])
    assert sim.calls("PATCH") == []
    assert sim.calls("GET", "/settings") != []
    assert len(sim.calls("GET", "/settings")) == 1  # checked once per process


async def test_only_the_differing_settings_are_applied() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=3)
    sim.settings["embedders"] = {"default": {"source": "userProvided", "dimensions": 2}}
    await sim.index(dimensions=3).upsert([index_document()])
    (patch,) = sim.calls("PATCH", "/settings")
    assert sim.body(patch) == {
        "embedders": {"default": {"source": "userProvided", "dimensions": 3}}
    }


async def test_a_document_is_sent_whole() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=2)
    contact, kind, tag = ContactId(uid(1)), DocumentTypeId(uid(2)), TagId(uid(3))
    document = index_document(
        id=DocumentId(uid(10)),
        version=5,
        owner_id=UserId(uid(11)),
        drawer_id=DrawerId(uid(12)),
        lane=None,
        title="Rechnung",
        text="Text",
        contact_id=contact,
        contact="Stadtwerke",
        document_type_id=kind,
        document_type="Rechnung",
        tag_ids=(tag,),
        tags=("Energie",),
        attributes=("Betrag: 12.50 EUR",),
        document_date=date(2026, 3, 3),
        vectors=((0.5, 0.25), (1.0, 0.0)),
        embedding=EmbeddingStamp("bge-m3", "d1"),
    )
    plain = index_document(lane=Lane.YELLOW)
    await sim.index(dimensions=2).upsert([document, plain])
    (write,) = sim.calls("POST", "/documents")
    first, second = sim.body(write)
    assert first["id"] == str(uid(10))
    assert (first["lane"], first["version"], first["contact_id"]) == ("processing", 5, str(uid(1)))
    assert first["tag_ids"] == [str(uid(3))]
    assert first["document_date"] == 1772496000  # 2026-03-03 00:00 UTC
    assert first["created_at"] == int(NOW.timestamp())
    assert first["_vectors"] == {"default": [[0.5, 0.25], [1.0, 0.0]]}
    assert (first["embedding_model"], first["embedding_digest"]) == ("bge-m3", "d1")
    # A document without vectors opts out of the embedder.
    assert second["_vectors"] == {"default": None}
    assert second["lane"] == "yellow"
    assert second["contact_id"] is None


async def test_an_index_without_embedder_sends_no_vectors() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    await sim.index().upsert([index_document()])
    (write,) = sim.calls("POST", "/documents")
    assert "_vectors" not in sim.body(write)[0]
    assert "embedders" not in sim.settings


async def test_vectors_of_the_wrong_length_never_leave_the_adapter() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=3)
    document = index_document(vectors=((1.0, 2.0),), embedding=EmbeddingStamp("m", "d"))
    with pytest.raises(SearchIndexError, match="needs 3"):
        await sim.index(dimensions=3).upsert([document])
    assert sim.calls("POST", "/documents") == []


async def test_writes_wait_for_their_task() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.pending_polls = 3
    await sim.index().upsert([index_document()])
    assert len(sim.calls("GET", "/tasks/1")) == 4


async def test_a_failed_task_is_an_error_with_its_code() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.task_status = "failed"
    sim.task_error = {"code": "invalid_vector_dimensions", "message": "embedder `default` needs 3"}
    with pytest.raises(SearchIndexError, match="invalid_vector_dimensions: embedder `default`"):
        await sim.index().upsert([index_document()])
    with pytest.raises(SearchIndexError, match="failed"):
        await sim.index().remove(DocumentId(uid(1)))


async def test_a_task_that_takes_too_long_is_unavailability() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.pending_polls = 10_000
    with pytest.raises(SearchUnavailableError, match="did not finish within"):
        await sim.index(task_timeout=0.05).upsert([index_document()])


async def test_unreachable_and_slow_meilisearch_are_unavailability() -> None:
    sim = SimulatedMeilisearch()

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    def stall(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    for failure, message in ((refuse, "cannot reach"), (stall, "did not answer in time")):
        sim.fail = failure
        with pytest.raises(SearchUnavailableError, match=message):
            await sim.index().upsert([index_document()])
        with pytest.raises(SearchUnavailableError, match=message):
            await sim.index().check()


async def test_server_errors_are_unavailability_and_client_errors_are_not() -> None:
    sim = SimulatedMeilisearch()
    sim.fail = lambda _: httpx2.Response(503, text="busy")
    with pytest.raises(SearchUnavailableError, match="HTTP 503"):
        await sim.index().search(_query())
    sim.fail = lambda _: httpx2.Response(
        401, json={"code": "invalid_api_key", "message": "The provided API key is invalid."}
    )
    with pytest.raises(SearchIndexError, match="invalid_api_key") as failure:
        await sim.index().search(_query())
    assert not isinstance(failure.value, SearchUnavailableError)


async def test_check_looks_at_health_and_key() -> None:
    sim = SimulatedMeilisearch()
    await sim.index().check()
    assert [request.url.path for request in sim.requests] == ["/health", "/version"]
    sim.fail = lambda request: (
        httpx2.Response(200, json={"status": "unhealthy"})
        if request.url.path == "/health"
        else None
    )
    with pytest.raises(SearchUnavailableError):
        await sim.index().check()


async def test_state_of_a_missing_document() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.fail = lambda request: (
        httpx2.Response(404, json={"code": "document_not_found", "message": "not found"})
        if "/documents/" in request.url.path
        else None
    )
    assert await sim.index().state(DocumentId(uid(1))) is None


async def test_the_search_request_and_its_answer() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=2)
    sim.search_answer = {
        "hits": [
            {
                "id": str(uid(1)),
                "version": 3,
                "_rankingScore": 0.75,
                "_formatted": {
                    "id": str(uid(1)),
                    "text": f"…die {HIGHLIGHT_START}Zahlung{HIGHLIGHT_END} ist offen",
                },
            },
            {"id": str(uid(2)), "version": 1},
        ],
        "estimatedTotalHits": 40,
    }
    result = await sim.index(dimensions=2).search(
        _query(vector=(0.5, 0.5), semantic_ratio=0.3, offset=20, limit=10)
    )
    (search,) = sim.calls("POST", "/search")
    body = sim.body(search)
    assert (body["q"], body["offset"], body["limit"]) == ("Zahlung", 20, 10)
    assert body["vector"] == [0.5, 0.5]
    assert body["hybrid"] == {"embedder": "default", "semanticRatio": 0.3}
    assert body["attributesToCrop"] == ["text"]
    assert body["highlightPreTag"] == HIGHLIGHT_START
    assert result.semantic
    assert result.estimated_total == 40
    first, second = result.hits
    assert (first.id, first.version, first.score) == (uid(1), 3, 0.75)
    assert first.snippet == (
        Segment("…die "),
        Segment("Zahlung", match=True),
        Segment(" ist offen"),
    )
    assert (second.score, second.snippet) == (None, ())


async def test_a_search_without_vector_or_with_ratio_zero_is_plain_full_text() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim, dimensions=2)
    index = sim.index(dimensions=2)
    for query in (_query(), _query(vector=(1.0, 0.0), semantic_ratio=0.0)):
        assert not (await index.search(query)).semantic
    for request in sim.calls("POST", "/search"):
        assert "hybrid" not in sim.body(request)
        assert "vector" not in sim.body(request)


async def test_vectors_need_an_embedder() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    with pytest.raises(SearchIndexError, match="no embedder"):
        await sim.index().search(_query(vector=(1.0,)))


async def test_a_search_without_lanes_finds_nothing_and_asks_nothing() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    result = await sim.index().search(_query(filter=DocumentFilter(lanes=frozenset())))
    assert result.hits == []
    assert sim.calls("POST", "/search") == []


async def test_the_rebuild_swaps_and_removes_the_old_index() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    index = sim.index()
    build = await index.begin_rebuild()
    await build.add([index_document()])
    await build.finish()
    steps = [
        (request.method, request.url.path)
        for request in sim.requests
        if request.url.path != "/tasks/1"
    ]
    assert ("DELETE", "/indexes/papiq-test-rebuild") in steps
    assert ("POST", "/indexes/papiq-test-rebuild/documents") in steps
    swap = [request for request in sim.requests if request.url.path == "/swap-indexes"]
    assert sim.body(swap[0]) == [{"indexes": ["papiq-test", "papiq-test-rebuild"]}]
    assert steps[-1] == ("DELETE", "/indexes/papiq-test-rebuild")
    assert steps.index(("POST", "/swap-indexes")) < len(steps) - 1


async def test_the_rebuild_goes_on_when_there_is_no_old_build_index() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.deleting_a_missing_index = True
    build = await sim.index().begin_rebuild()  # no error although there was nothing to delete
    await build.abort()


async def test_deleting_an_index_fails_on_other_errors() -> None:
    sim = SimulatedMeilisearch()
    sim.settings = wanted_settings(sim)
    sim.task_status, sim.task_error = "failed", {"code": "internal", "message": "disk full"}
    with pytest.raises(SearchIndexError, match="disk full"):
        await (await sim.index().begin_rebuild()).abort()


# --- filters and segments --------------------------------------------------------------------


def _query(**fields: Any) -> SearchQuery:
    visibility = Visibility(UserId(uid(100)), frozenset())
    return SearchQuery(text="Zahlung", visibility=visibility, **fields)


def test_the_filter_for_a_user_without_drawers() -> None:
    assert filter_expression(_query()) == f'(owner_id = "{uid(100)}")'


def test_the_filter_for_a_user_with_drawers_and_criteria() -> None:
    visibility = Visibility(UserId(uid(100)), frozenset({DrawerId(uid(2)), DrawerId(uid(1))}))
    query = SearchQuery(
        text="x",
        visibility=visibility,
        filter=DocumentFilter(
            contact=ContactId(uid(3)),
            document_type=DocumentTypeId(uid(4)),
            tags=frozenset({TagId(uid(6)), TagId(uid(5))}),
            drawer=DrawerId(uid(1)),
            lanes=frozenset({Lane.GREEN, None}),
        ),
    )
    assert filter_expression(query) == (
        f'(owner_id = "{uid(100)}" OR (lane = "green" AND drawer_id IN '
        f'["{uid(1)}", "{uid(2)}"])) '
        f'AND contact_id = "{uid(3)}" AND document_type_id = "{uid(4)}" '
        f'AND tag_ids = "{uid(5)}" AND tag_ids = "{uid(6)}" AND drawer_id = "{uid(1)}" '
        'AND lane IN ["green", "processing"]'
    )


def test_segments() -> None:
    mark = HIGHLIGHT_START
    end = HIGHLIGHT_END
    assert segments("") == ()
    assert segments("plain") == (Segment("plain"),)
    assert segments(f"{mark}all{end}") == (Segment("all", match=True),)
    assert segments(f"a {mark}b{end} c {mark}d{end}") == (
        Segment("a "),
        Segment("b", match=True),
        Segment(" c "),
        Segment("d", match=True),
    )
    assert segments(f"open {mark}never closed") == (
        Segment("open "),
        Segment("never closed", match=True),
    )

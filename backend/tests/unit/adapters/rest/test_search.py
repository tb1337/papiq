"""Search over HTTP: who finds what, the parameters, the errors, the rebuild."""

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import SearchUnavailableError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.pipeline import Step
from tests.api import auth
from tests.unit.adapters.rest.conftest import Api
from tests.unit.adapters.rest.test_permission_matrix import ACTORS, DENIED, Scene, build_scene

SEARCH = f"{PREFIX}/documents/search"


async def indexed_scene(api: Api) -> Scene:
    """The scene of the permission matrix, with its documents in the search index."""
    scene = await build_scene(api)
    indexing = api.services.indexing
    assert indexing is not None
    await indexing.reconcile()
    while await indexing.run_next_job():
        pass
    return scene


async def found(scene: Scene, actor: str, **params: Any) -> set[str]:
    ids: set[str] = set()
    offset: int | None = 0
    while offset is not None:
        response = await scene.api.client.get(
            SEARCH,
            params={"q": "pdf", "limit": 1, **params, "offset": offset},
            headers=scene.headers[actor],
        )
        assert response.status_code == 200, response.text
        page = response.json()
        ids |= {item["document"]["id"] for item in page["items"]}
        offset = page["next_offset"]
    return ids


async def test_search_shows_only_what_the_caller_may_read(api: Api) -> None:
    scene = await indexed_scene(api)
    everything = {scene.green, scene.yellow, scene.processing}
    assert await found(scene, "owner") == everything
    assert await found(scene, "read_token") == everything
    for actor in ("reader", "writer"):
        assert await found(scene, actor) == {scene.green}
        assert await found(scene, actor, lane="yellow") == set()
        assert await found(scene, actor, lane="processing") == set()
        assert await found(scene, actor, drawer_id=str(scene.shared.id)) == {scene.green}
    for actor in ("stranger", "admin"):
        assert await found(scene, actor) == set()
        assert await found(scene, actor, drawer_id=str(scene.shared.id)) == set()
    assert await found(scene, "owner", lane=["yellow", "processing"]) == {
        scene.yellow,
        scene.processing,
    }


@pytest.mark.parametrize("actor", sorted(DENIED))
async def test_search_needs_a_valid_sign_in(api: Api, actor: str) -> None:
    scene = await indexed_scene(api)
    response = await api.client.get(SEARCH, params={"q": "pdf"}, headers=scene.headers[actor])
    assert response.status_code == 401
    anonymous = await api.client.get(SEARCH, params={"q": "pdf"})
    assert anonymous.status_code == 401


def test_every_actor_is_covered() -> None:
    assert set(ACTORS) >= set(DENIED)


async def test_a_withdrawn_share_ends_the_search_at_once(api: Api) -> None:
    scene = await indexed_scene(api)
    assert await found(scene, "reader") == {scene.green}
    owner_drawers = api.services.drawers
    shared = await owner_drawers.get(scene.owner.id, scene.shared.id)
    reader_id = next(user for user, level in shared.shares.items() if level is ShareLevel.READ)
    await owner_drawers.unshare(scene.owner.id, scene.shared.id, reader_id)
    assert await found(scene, "reader") == set()
    assert await found(scene, "writer") == {scene.green}


async def test_a_lane_that_changed_before_the_index_heard_of_it(api: Api) -> None:
    scene = await indexed_scene(api)
    pipeline = api.services.pipeline
    await pipeline.reprocess_from(scene.owner.id, DocumentId(UUID(scene.green)), Step.CLASSIFY)
    assert await found(scene, "reader") == set()  # in processing again; the index says green
    assert await found(scene, "owner") >= {scene.green}


async def test_a_hit_has_the_document_a_snippet_and_a_score(api: Api) -> None:
    scene = await indexed_scene(api)
    response = await api.client.get(
        SEARCH, params={"q": "fake text"}, headers=scene.headers["reader"]
    )
    assert response.status_code == 200
    page = response.json()
    (item,) = page["items"]
    assert item["document"]["id"] == scene.green
    assert item["document"]["access"] == "read"
    assert "".join(part["text"] for part in item["snippet"]).strip()
    assert any(part["match"] for part in item["snippet"])
    assert 0 <= item["score"] <= 1
    assert page["semantic"] is False  # no embeddings in this installation
    assert page["next_offset"] is None
    assert page["estimated_total"] >= 1


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 501},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 101},
        {"q": "x", "offset": -1},
        {"q": "x", "offset": 1001},
        {"q": "x", "semantic_ratio": 1.1},
        {"q": "x", "lane": "blue"},
        {"q": "x", "contact_id": "nonsense"},
    ],
)
async def test_bad_parameters_are_unprocessable(api: Api, params: dict[str, Any]) -> None:
    user = await api.user()
    response = await api.client.get(SEARCH, params=params, headers=auth(user))
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_search_is_not_a_document_id(api: Api) -> None:
    user = await api.user()
    response = await api.client.get(f"{PREFIX}/documents/search", headers=auth(user))
    assert response.status_code == 422
    assert "q:" in response.json()["detail"]


async def test_without_a_search_index_the_endpoints_answer_503(api: Api) -> None:
    admin = await api.admin()
    api.app.state.context = replace(api.app.state.context, search=None, indexing=None)
    found = await api.client.get(SEARCH, params={"q": "x"}, headers=auth(admin))
    rebuild = await api.client.post(f"{PREFIX}/search/reindex", headers=auth(admin))
    for response in (found, rebuild):
        assert response.status_code == 503
        assert response.json()["detail"] == "search is not configured"


async def test_an_unreachable_index_answers_503(api: Api) -> None:
    user = await api.user()

    async def down(*_: object, **__: object) -> None:
        raise SearchUnavailableError("the search index cannot be reached")

    assert api.container.search_index is not None
    api.container.search_index.search = down  # type: ignore[method-assign,assignment]
    response = await api.client.get(SEARCH, params={"q": "x"}, headers=auth(user))
    assert response.status_code == 503
    assert response.json()["detail"] == "the search index cannot be reached"


async def test_only_admins_rebuild_the_index(api: Api) -> None:
    scene = await indexed_scene(api)
    url = f"{PREFIX}/search/reindex"
    expected = {
        "owner": 403,
        "reader": 403,
        "stranger": 403,
        "read_token": 403,
        **DENIED,
    }
    for actor, status in expected.items():
        response = await api.client.post(url, headers=scene.headers[actor])
        assert response.status_code == status, actor
    for _ in range(2):  # a rebuild that is queued is not queued twice
        response = await api.client.post(url, headers=scene.headers["admin"])
        assert response.status_code == 202
        assert response.content == b""
    indexing = api.services.indexing
    assert indexing is not None
    assert await indexing.run_next_job()
    assert not await indexing.run_next_job()
    assert await found(scene, "owner") == {scene.green, scene.yellow, scene.processing}

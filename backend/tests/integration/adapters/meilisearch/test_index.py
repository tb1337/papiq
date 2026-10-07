"""The Meilisearch adapter against a real Meilisearch (the devcontainer service)."""

import secrets

import httpx2

from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.composition.settings import Settings
from papiq.core.domain.ids import UserId, new_id
from papiq.core.domain.search import Visibility
from papiq.core.ports import SearchQuery
from tests.builders import index_document
from tests.contracts.search_index import SearchIndexContract
from tests.integration.adapters.meilisearch.conftest import drop, new_index


class TestMeilisearchIndex(SearchIndexContract):
    pass


async def test_compound_words_are_split(search_index: MeilisearchIndex) -> None:
    owner = UserId(new_id())
    document = index_document(owner_id=owner, title="Stromrechnung", text="Ihre Jahresabrechnung")
    await search_index.upsert([document])
    for word in ("Strom", "rechnung", "Abrechnung"):
        result = await search_index.search(
            SearchQuery(text=word, visibility=Visibility(owner, frozenset()))
        )
        assert [hit.id for hit in result.hits] == [document.id], word


async def test_the_settings_are_applied_once(
    meilisearch_settings: Settings, search_index: MeilisearchIndex
) -> None:
    """A second adapter on the same index finds the settings in place and changes nothing, so a
    restart does not index again."""
    await search_index.check()
    await search_index.upsert([index_document()])
    name = search_index._index
    again = new_index(meilisearch_settings, name)
    try:
        before = await _settings_tasks(meilisearch_settings, name)
        await again.upsert([index_document()])
        assert await _settings_tasks(meilisearch_settings, name) == before
    finally:
        await again.aclose()
    assert before >= 1


async def _settings_tasks(settings: Settings, name: str) -> int:
    assert settings.meilisearch_url is not None
    key = settings.meilisearch_api_key
    headers = {} if key is None else {"Authorization": f"Bearer {key.get_secret_value()}"}
    async with httpx2.AsyncClient(
        base_url=str(settings.meilisearch_url), headers=headers
    ) as client:
        response = await client.get("/tasks", params={"indexUids": name, "types": "settingsUpdate"})
    return int(response.json()["total"])


async def test_an_index_without_embedder_takes_documents_without_vectors(
    meilisearch_settings: Settings,
) -> None:
    name = f"papiq-test-{secrets.token_hex(6)}"
    index = new_index(meilisearch_settings, name, dimensions=None)
    try:
        owner = UserId(new_id())
        document = index_document(owner_id=owner, title="Mahnung")
        await index.upsert([document])
        result = await index.search(
            SearchQuery(text="Mahnung", visibility=Visibility(owner, frozenset()))
        )
        assert [hit.id for hit in result.hits] == [document.id]
    finally:
        await index.aclose()
        await drop(meilisearch_settings, name)

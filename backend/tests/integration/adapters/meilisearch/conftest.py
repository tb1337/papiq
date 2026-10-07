import secrets
from collections.abc import AsyncIterator

import httpx2
import pytest

from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.composition.settings import Settings
from tests import probes
from tests.contracts.search_index import DIMENSIONS


@pytest.fixture(scope="session")
def meilisearch_settings(settings: Settings) -> Settings:
    if settings.meilisearch_url is None:
        pytest.skip("PAPIQ_MEILISEARCH_URL is not set")
    if probes.meilisearch_health(str(settings.meilisearch_url)) is None:
        pytest.skip(f"Meilisearch not reachable at {settings.meilisearch_url}")
    return settings


def new_index(
    settings: Settings, name: str, *, dimensions: int | None = DIMENSIONS
) -> MeilisearchIndex:
    assert settings.meilisearch_url is not None
    key = settings.meilisearch_api_key
    return MeilisearchIndex(
        url=str(settings.meilisearch_url),
        api_key=None if key is None else key.get_secret_value(),
        index=name,
        dimensions=dimensions,
    )


async def drop(settings: Settings, *names: str) -> None:
    """Delete test indexes, whatever state they are in."""
    assert settings.meilisearch_url is not None
    key = settings.meilisearch_api_key
    headers = {} if key is None else {"Authorization": f"Bearer {key.get_secret_value()}"}
    async with httpx2.AsyncClient(
        base_url=str(settings.meilisearch_url), headers=headers
    ) as client:
        for name in names:
            await client.delete(f"/indexes/{name}")


@pytest.fixture
async def search_index(meilisearch_settings: Settings) -> AsyncIterator[MeilisearchIndex]:
    """An empty index of its own that takes vectors of `DIMENSIONS` numbers; removed afterwards."""
    name = f"papiq-test-{secrets.token_hex(6)}"
    index = new_index(meilisearch_settings, name)
    try:
        yield index
    finally:
        await index.aclose()
        await drop(meilisearch_settings, name, f"{name}-rebuild")

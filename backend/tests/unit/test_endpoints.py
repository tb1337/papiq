from typing import Any

import pytest

from papiq.composition.endpoints import external_endpoints, is_local_host
from papiq.composition.settings import Settings


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        "10.30.2.15",
        "192.168.1.2",
        "172.16.0.1",
        "fd00::1",
        "169.254.1.1",
        "ollama",
        "host.docker.internal",
        "macmini.local",
        "nas.lan",
        "nas.home.arpa",
    ],
)
def test_local_hosts(host: str) -> None:
    assert is_local_host(host)


@pytest.mark.parametrize(
    "host", ["api.openai.com", "api.example.com", "8.8.8.8", "2001:4860:4860::8888"]
)
def test_external_hosts(host: str) -> None:
    assert not is_local_host(host)


def test_external_endpoints() -> None:
    values: dict[str, Any] = {
        "llm_base_url": "http://ollama:11434/v1",
        "llm_model": "m",
        "embedding_base_url": "https://api.example.com/v1",
        "embedding_model": "e",
    }
    settings = Settings(**values)
    assert external_endpoints(settings) == {"PAPIQ_EMBEDDING_BASE_URL": "api.example.com"}
    assert external_endpoints(Settings()) == {}


def test_search_index_content_leaves_the_network() -> None:
    external: dict[str, Any] = {"meilisearch_url": "https://search.example.com"}
    assert external_endpoints(Settings(**external)) == {
        "PAPIQ_MEILISEARCH_URL": "search.example.com"
    }
    local: dict[str, Any] = {"meilisearch_url": "http://meilisearch:7700"}
    assert external_endpoints(Settings(**local)) == {}

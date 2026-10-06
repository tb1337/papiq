from typing import Any

import pytest

from papiq.composition import container
from papiq.composition.container import Persistence, build_container
from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings


def settings(**values: Any) -> Settings:
    return Settings(**values)


def fake_persistence(_: Settings) -> Persistence:
    return Persistence(unit_of_work=object, event_bus=object())  # type: ignore[arg-type]


def test_missing_adapter_is_reported_with_port_and_name() -> None:
    with pytest.raises(AdapterNotAvailableError) as info:
        build_container(settings())
    assert str(info.value) == "persistence: adapter 'sqlite' is not implemented yet"
    assert (info.value.port, info.value.adapter) == ("persistence", "sqlite")


def test_selection_follows_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = settings(db_type="postgres", db_host="h", db_name="n", db_user="u", db_password="p")
    with pytest.raises(AdapterNotAvailableError, match="persistence: adapter 'postgres'"):
        build_container(postgres)

    monkeypatch.setitem(container.PERSISTENCE, "postgres", fake_persistence)
    with pytest.raises(AdapterNotAvailableError, match="object_store: adapter 'filesystem'"):
        build_container(postgres)


def test_each_port_is_checked_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    with pytest.raises(AdapterNotAvailableError, match="object_store: adapter 'filesystem'"):
        build_container(settings())

    s3 = settings(
        storage_type="s3",
        s3_endpoint_url="http://garage:3900",
        s3_bucket="b",
        s3_access_key_id="k",
        s3_secret_access_key="s",
    )
    with pytest.raises(AdapterNotAvailableError, match="object_store: adapter 's3'"):
        build_container(s3)


def test_optional_ports_are_only_selected_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    tables: list[dict[str, Any]] = [
        container.OBJECT_STORES,
        container.OCR_ENGINES,
        container.PARSERS,
        container.IDENTITY_PROVIDERS,
    ]
    names = ["filesystem", "ocrmypdf", "docling", "native"]
    for table, name in zip(tables, names, strict=True):
        monkeypatch.setitem(table, name, lambda _: object())

    built = build_container(settings())
    assert built.search_index is None
    assert built.language_model is None
    assert built.embeddings is None

    with pytest.raises(AdapterNotAvailableError, match="search_index: adapter 'meilisearch'"):
        build_container(settings(meilisearch_url="http://meilisearch:7700"))
    with pytest.raises(AdapterNotAvailableError, match="llm: adapter 'openai-compatible'"):
        build_container(settings(llm_base_url="http://ollama:11434/v1", llm_model="m"))
    with pytest.raises(AdapterNotAvailableError, match="embeddings: adapter 'openai-compatible'"):
        build_container(settings(embedding_base_url="http://ollama:11434/v1", embedding_model="m"))

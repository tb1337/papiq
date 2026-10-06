from pathlib import Path
from typing import Any

import pytest

from papiq.adapters.outbound.memory import ManualClock
from papiq.adapters.outbound.sql import SqlEventBus, SqlUnitOfWorkFactory
from papiq.adapters.outbound.system import SystemClock
from papiq.composition import container
from papiq.composition.container import (
    Persistence,
    build_container,
    build_memory_container,
    build_services,
)
from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.users import Role
from tests import builders
from tests.builders import NOW


def settings(**values: Any) -> Settings:
    return Settings(**values)


def fake_persistence(_: Settings) -> Persistence:
    return Persistence(unit_of_work=object, event_bus=object())  # type: ignore[arg-type]


def test_missing_adapter_is_reported_with_port_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    with pytest.raises(AdapterNotAvailableError) as info:
        build_container(settings())
    assert str(info.value) == "object_store: adapter 'filesystem' is not implemented yet"
    assert (info.value.port, info.value.adapter) == ("object_store", "filesystem")


def test_the_database_type_selects_the_sql_adapter(tmp_path: Path) -> None:
    for configured in (
        settings(db_sqlite_path=tmp_path / "papiq.db"),
        settings(db_type="postgres", db_host="h", db_name="n", db_user="u", db_password="p"),
    ):
        persistence = container.PERSISTENCE[configured.db_type](configured)
        assert isinstance(persistence.unit_of_work, SqlUnitOfWorkFactory)
        assert isinstance(persistence.event_bus, SqlEventBus)
    assert list(tmp_path.iterdir()) == []  # nothing is created before `migrate`


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


async def test_memory_container_runs_the_core() -> None:
    clock = ManualClock(NOW)
    built = build_memory_container(clock)
    assert built.clock is clock
    services = build_services(built)

    admin = builders.admin()
    async with built.unit_of_work() as uow:
        await uow.users.add(admin)
        await uow.commit()
    user = await services.users.create_user(admin.id, "dana", Role.USER)

    received: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        received.append(event)

    built.event_bus.subscribe("test", record)
    document = await services.pipeline.receive(
        user.id, b"%PDF", filename="a.pdf", media_type="application/pdf"
    )
    while await services.pipeline.run_next_job():
        pass
    assert (await services.documents.get(user.id, document.id)).lane is Lane.GREEN
    await built.event_bus.dispatch()
    assert received[0].type == "document.received"
    assert received[-1].type == "document.lane_changed"


def test_memory_containers_are_independent() -> None:
    first, second = build_memory_container(), build_memory_container()
    assert first.unit_of_work is not second.unit_of_work
    assert isinstance(first.clock, SystemClock)

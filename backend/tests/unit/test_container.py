from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from papiq.adapters.outbound.docling import DoclingParser
from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.memory import ManualClock
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.adapters.outbound.sql import SqlEventBus, SqlUnitOfWorkFactory
from papiq.adapters.outbound.system import SystemClock
from papiq.composition import container
from papiq.composition.container import (
    Closer,
    Persistence,
    build_container,
    build_memory_container,
    build_services,
)
from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.domain.users import Role
from papiq.core.services.pipeline import RetryPolicy
from papiq.core.services.steps import OcrStep, ParseStep
from tests import builders
from tests.builders import NOW, incoming
from tests.contracts.processing import SAMPLES


def settings(**values: Any) -> Settings:
    return Settings(**values)


async def _no_op() -> None:
    pass


def fake_persistence(_: Settings) -> Persistence:
    return Persistence(unit_of_work=object, event_bus=object(), close=_no_op)  # type: ignore[arg-type]


def test_missing_adapter_is_reported_with_port_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    monkeypatch.delitem(container.OBJECT_STORES, "filesystem")
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


def test_the_storage_type_selects_the_object_store(tmp_path: Path) -> None:
    filesystem = container.OBJECT_STORES["filesystem"](settings(storage_path=tmp_path / "o"))
    assert isinstance(filesystem, FilesystemObjectStore)
    s3 = container.OBJECT_STORES["s3"](
        settings(
            storage_type="s3",
            s3_endpoint_url="http://garage:3900",
            s3_bucket="b",
            s3_access_key_id="k",
            s3_secret_access_key="s",
        )
    )
    assert isinstance(s3, S3ObjectStore)
    assert list(tmp_path.iterdir()) == []  # directories are created on the first write


def test_each_port_is_checked_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    monkeypatch.delitem(container.OCR_ENGINES, "ocrmypdf")
    monkeypatch.delitem(container.PARSERS, "docling")
    with pytest.raises(AdapterNotAvailableError, match="ocr: adapter 'ocrmypdf'"):
        build_container(settings())
    monkeypatch.setitem(container.OCR_ENGINES, "ocrmypdf", lambda _: object())
    with pytest.raises(AdapterNotAvailableError, match="parser: adapter 'docling'"):
        build_container(settings())


def test_processing_adapters_follow_the_settings(tmp_path: Path) -> None:
    configured = settings(
        ocr_languages="deu+eng+fra",
        ocr_timeout="120",
        parse_timeout="300",
        docling_models_path=tmp_path,
        storage_path=tmp_path,
    )
    ocr = container.OCR_ENGINES["ocrmypdf"](configured)
    assert isinstance(ocr, OcrmypdfEngine)
    assert (ocr._languages, ocr._timeout) == ("deu+eng+fra", timedelta(minutes=2))
    parser = container.PARSERS["docling"](configured)
    assert isinstance(parser, DoclingParser)
    assert (parser._models, parser._timeout) == (tmp_path, timedelta(minutes=5))


def test_services_take_their_tuning_from_the_settings() -> None:
    configured = settings(
        step_max_attempts="5",
        step_retry_delay="10",
        ocr_timeout="120",
        parse_timeout="300",
        cleanup_interval="60",
        retention="P1D",
    )
    services = build_services(build_memory_container(), configured)
    pipeline = services.pipeline
    assert pipeline._retry == RetryPolicy(max_attempts=5, delay=timedelta(seconds=10))
    assert pipeline._lease == timedelta(minutes=5) + container.LEASE_MARGIN
    assert isinstance(pipeline._executors[Step.OCR], OcrStep)
    assert isinstance(pipeline._executors[Step.PARSE], ParseStep)
    maintenance = services.maintenance
    assert (maintenance._interval, maintenance._retention) == (
        timedelta(minutes=1),
        timedelta(days=1),
    )


async def test_closing_runs_every_closer_in_reverse_order() -> None:
    closed: list[str] = []

    def closer(name: str, fail: bool = False) -> Closer:
        async def close() -> None:
            closed.append(name)
            if fail:
                raise RuntimeError(name)

        return close

    built = replace(
        build_memory_container(),
        closers=(closer("database"), closer("store", fail=True), closer("other")),
    )
    with pytest.raises(ExceptionGroup) as info:
        await built.aclose()
    assert closed == ["other", "store", "database"]
    assert [str(error) for error in info.value.exceptions] == ["store"]


def test_optional_ports_are_only_selected_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(container.PERSISTENCE, "sqlite", fake_persistence)
    tables: list[dict[str, Any]] = [container.OCR_ENGINES, container.PARSERS]
    names = ["ocrmypdf", "docling"]
    for table, name in zip(tables, names, strict=True):
        monkeypatch.setitem(table, name, lambda _: object())

    built = build_container(settings())
    assert built.search_index is None
    assert built.language_model is None
    assert built.embeddings is None
    assert built.identity is None  # until M4

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
        user.id, incoming((SAMPLES / "scan.pdf").read_bytes()), filename="a.pdf"
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

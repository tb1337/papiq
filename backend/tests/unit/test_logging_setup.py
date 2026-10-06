import json
import logging

import pytest
import structlog

from papiq.composition.logging_setup import configure_logging
from papiq.composition.settings import Settings


def settings(**values: str) -> Settings:
    return Settings(**values)  # type: ignore[arg-type]


def test_json_format_writes_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json"))
    structlog.get_logger("papiq.test").info("document received", document_id="42")
    logging.getLogger("papiq.stdlib").warning("from the core")

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["event"] == "document received"
    assert first["document_id"] == "42"
    assert first["level"] == "info"
    assert first["timestamp"].endswith("Z")
    assert second["event"] == "from the core"
    assert second["logger"] == "papiq.stdlib"
    assert second["level"] == "warning"


def test_console_format_is_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="console"))
    structlog.get_logger("papiq.test").info("document received", document_id="42")

    output = capsys.readouterr().err
    assert "document received" in output
    assert "document_id=42" in output
    assert not output.lstrip().startswith("{")


def test_level_filters_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json", log_level="WARNING"))
    logging.getLogger("papiq.test").info("hidden")
    logging.getLogger("papiq.test").error("shown")

    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert events == ["shown"]


def test_exceptions_are_rendered(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json"))
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("papiq.test").exception("failed")

    record = json.loads(capsys.readouterr().err.splitlines()[0])
    assert "RuntimeError: boom" in record["exception"]

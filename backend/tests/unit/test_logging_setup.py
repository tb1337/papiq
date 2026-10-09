import json
import logging
from typing import Any

import pytest
import structlog

from papiq.adapters.outbound.webhooks import HttpWebhookSender
from papiq.composition.logging_setup import configure_logging
from papiq.composition.settings import Settings
from tests.contracts.webhook_sender import request
from tests.unit.adapters.webhooks.receiver import LocalReceiver


def settings(**values: Any) -> Settings:
    return Settings(**values)


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


def test_the_access_log_hides_the_oidc_callback_query(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json"))
    access = logging.getLogger("uvicorn.access")
    access.info(
        '%s - "%s %s HTTP/%s" %d',
        "192.0.2.1:5000",
        "GET",
        "/api/v1/auth/oidc/callback?code=secret-code&state=secret-state",
        "1.1",
        303,
    )
    access.info(
        '%s - "%s %s HTTP/%s" %d', "192.0.2.1:5000", "GET", "/api/v1/documents?x=1", "1.1", 200
    )
    output = capsys.readouterr().err
    assert "secret" not in output
    assert "/api/v1/auth/oidc/callback" in output
    assert "/api/v1/documents?x=1" in output


def test_the_access_log_leaves_out_the_health_checks(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json"))
    access = logging.getLogger("uvicorn.access")
    for path, status in (
        ("/api/v1/health", 200),
        ("/api/v1/health?verbose=1", 503),
        ("/api/v1/healthy", 404),
        ("/api/v1/documents", 200),
    ):
        access.info('%s - "%s %s HTTP/%s" %d', "172.18.0.1:5000", "GET", path, "1.1", status)
    lines = capsys.readouterr().err.splitlines()
    assert ["/api/v1/healthy" in line or "/api/v1/documents" in line for line in lines] == [
        True,
        True,
    ]


async def test_a_webhook_url_never_reaches_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json", log_level="DEBUG"))
    async with LocalReceiver() as receiver:
        await HttpWebhookSender().send(request(f"{receiver.url}/hooks/capability-token?key=abc"))

    assert "capability-token" not in capsys.readouterr().err

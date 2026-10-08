import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from papiq.adapters.outbound.memory import MemorySearchIndex
from papiq.composition import container
from papiq.composition.__main__ import main
from tests.builders import PASSWORD, SECRET_KEY, TRUSTED_PROXY


def test_valid_configuration_exits_zero_and_masks_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    secret = tmp_path / "key"
    secret.write_text("top-secret")
    monkeypatch.setenv("PAPIQ_MEILISEARCH_API_KEY_FILE", str(secret))
    monkeypatch.setenv("PAPIQ_DB_TYPO", "x")

    assert main() == 0

    err = capsys.readouterr().err
    assert "top-secret" not in err
    events = [json.loads(line) for line in err.splitlines()]
    assert [e["event"] for e in events] == ["unknown configuration variable", "configuration valid"]
    assert events[0]["variable"] == "PAPIQ_DB_TYPO"
    assert events[1]["meilisearch_api_key"] == "**********"


def test_external_language_models_are_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPIQ_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("PAPIQ_LLM_MODEL", "large")
    monkeypatch.setenv("PAPIQ_EMBEDDING_BASE_URL", "http://10.30.2.15:11434/v1")
    monkeypatch.setenv("PAPIQ_EMBEDDING_MODEL", "small")

    assert main() == 0

    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    warnings = [e for e in events if e["level"] == "warning"]
    assert [(e["variable"], e["host"]) for e in warnings] == [
        ("PAPIQ_LLM_BASE_URL", "api.example.com")
    ]
    assert "outside the local network" in warnings[0]["event"]


def test_invalid_configuration_exits_one_with_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPIQ_DB_TYPE", "mysql")

    assert main() == 1

    assert "PAPIQ_DB_TYPE" in capsys.readouterr().err


def test_migrate_sets_up_an_empty_sqlite_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "data" / "papiq.db"
    monkeypatch.setenv("PAPIQ_DB_SQLITE_PATH", str(path))

    assert main(["migrate"]) == 0
    assert main(["migrate"]) == 0  # up to date: nothing to do

    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"documents", "jobs", "outbox", "alembic_version"} <= tables
    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert events.count("database migrated") == 2


def test_reindex_needs_a_search_index(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reindex"]) == 1
    assert "PAPIQ_MEILISEARCH_URL is not set" in capsys.readouterr().err


def test_reindex_rebuilds_the_index(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("PAPIQ_DB_SQLITE_PATH", str(tmp_path / "papiq.db"))
    monkeypatch.setenv("PAPIQ_MEILISEARCH_URL", "http://meilisearch:7700")
    index = MemorySearchIndex()
    monkeypatch.setitem(container.SEARCH_INDEXES, "meilisearch", lambda _: index)
    for table, name in ((container.OCR_ENGINES, "ocrmypdf"), (container.PARSERS, "docling")):
        monkeypatch.setitem(table, name, lambda _: object())
    assert main(["migrate"]) == 0

    assert main(["reindex"]) == 0

    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    done = [event for event in events if event["event"] == "reindex finished"]
    assert [(e["documents"], e["queued"], e["removed"]) for e in done] == [(0, 0, 0)]


def test_failed_migration_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in {
        "DB_TYPE": "postgres",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",  # nothing listens here
        "DB_NAME": "papiq",
        "DB_USER": "papiq",
        "DB_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(f"PAPIQ_{name}", value)

    assert main(["migrate"]) == 1

    assert "database migration failed" in capsys.readouterr().err


def test_a_service_only_runs_in_its_role(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPIQ_ROLE", "api")

    assert main(["worker"]) == 1
    monkeypatch.setenv("PAPIQ_ROLE", "worker")
    assert main(["api"]) == 1

    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert "the worker does not run with PAPIQ_ROLE=api" in events
    assert events[-1] == "the api does not run with PAPIQ_ROLE=worker"


def test_the_worker_stops_cleanly_on_sigterm(tmp_path: Path) -> None:
    """In a process of its own: start on SQLite and the filesystem, then SIGTERM."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PAPIQ_")}
    environment |= {
        "PAPIQ_DB_SQLITE_PATH": str(tmp_path / "papiq.db"),
        "PAPIQ_STORAGE_PATH": str(tmp_path / "objects"),
        "PAPIQ_LOG_LEVEL": "INFO",
        "PAPIQ_SECRET_KEY": SECRET_KEY,
        "PAPIQ_FORWARDED_ALLOW_IPS": TRUSTED_PROXY,
    }
    command = [sys.executable, "-m", "papiq.composition"]
    subprocess.run([*command, "migrate"], env=environment, check=True, capture_output=True)

    with subprocess.Popen(
        [*command, "worker"], env=environment, stderr=subprocess.PIPE, text=True
    ) as process:
        assert process.stderr is not None
        lines: list[str] = []
        for line in process.stderr:
            lines.append(line)
            if json.loads(line)["event"] == "worker started":
                break
        process.send_signal(signal.SIGTERM)
        lines += process.stderr.readlines()
        assert process.wait(timeout=30) == 0
    events = [json.loads(line)["event"] for line in lines]
    assert events[-2:] == ["worker stopping", "worker stopped"]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_the_api_serves_health_and_stops_on_sigterm(tmp_path: Path) -> None:
    """In a process of its own: start on SQLite and the filesystem, ask /health, SIGTERM. The
    first admin is created from the configuration; the password never shows in the log."""
    port = free_port()
    password_file = tmp_path / "admin-password"
    password_file.write_text(PASSWORD + "\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PAPIQ_")}
    environment |= {
        "PAPIQ_DB_SQLITE_PATH": str(tmp_path / "papiq.db"),
        "PAPIQ_STORAGE_PATH": str(tmp_path / "objects"),
        "PAPIQ_API_HOST": "127.0.0.1",
        "PAPIQ_API_PORT": str(port),
        "PAPIQ_SECRET_KEY": SECRET_KEY,
        "PAPIQ_FORWARDED_ALLOW_IPS": TRUSTED_PROXY,
        "PAPIQ_ADMIN_USERNAME": "admin",
        "PAPIQ_ADMIN_PASSWORD_FILE": str(password_file),
    }
    command = [sys.executable, "-m", "papiq.composition"]
    subprocess.run([*command, "migrate"], env=environment, check=True, capture_output=True)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with subprocess.Popen(
        [*command, "api"], env=environment, stderr=subprocess.PIPE, text=True
    ) as process:
        assert process.stderr is not None
        deadline = time.monotonic() + 30
        health = None
        while time.monotonic() < deadline and health is None:
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as answer:
                    health = json.load(answer)
            except OSError:
                time.sleep(0.1)
        login = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/auth/login",
            data=json.dumps({"username": "admin", "password": PASSWORD}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(login, timeout=5) as answer:
            signed_in = json.load(answer)
            cookie = answer.headers["Set-Cookie"]
        process.send_signal(signal.SIGTERM)
        output = process.stderr.read()
        assert process.wait(timeout=30) == 0, output
    assert health == {"status": "ok", "checks": {"database": "ok", "object_store": "ok"}}
    assert signed_in["user"]["role"] == "admin"
    assert cookie.startswith("__Host-papiq_session=") and "Secure" in cookie
    assert "first admin created" in output
    assert PASSWORD not in output


def _events(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]


def test_check_schema_tells_whether_the_database_is_migrated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "data" / "papiq.db"
    monkeypatch.setenv("PAPIQ_DB_SQLITE_PATH", str(path))

    assert main(["check-schema"]) == 1
    assert not path.exists()  # looking does not create the file
    assert "the database schema is not migrated" in _events(capsys)

    assert main(["migrate"]) == 0
    capsys.readouterr()
    assert main(["check-schema"]) == 0
    assert "database schema is current" in _events(capsys)


def test_check_schema_rejects_a_database_of_a_newer_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "papiq.db"
    monkeypatch.setenv("PAPIQ_DB_SQLITE_PATH", str(path))
    assert main(["migrate"]) == 0
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999'")
    capsys.readouterr()

    started = time.monotonic()
    assert main(["check-schema", "--wait", "30"]) == 1  # no waiting: it will not get better

    assert time.monotonic() - started < 10
    assert any("newer version" in event for event in _events(capsys))


def test_check_schema_waits_for_the_migration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "papiq.db"
    monkeypatch.setenv("PAPIQ_DB_SQLITE_PATH", str(path))
    monkeypatch.setattr("papiq.composition.database.SCHEMA_POLL_INTERVAL", 0.1)

    timer = threading.Timer(1.0, lambda: main(["migrate"]))
    timer.start()
    try:
        assert main(["check-schema", "--wait", "30"]) == 0
    finally:
        timer.join()

    assert "waiting for the database schema to be migrated" in _events(capsys)


def test_wait_only_goes_with_check_schema(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["migrate", "--wait", "5"])
    assert "--wait only goes with check-schema" in capsys.readouterr().err

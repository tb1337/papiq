import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from papiq.composition.__main__ import main


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
    """In a process of its own: start on SQLite and the filesystem, ask /health, SIGTERM."""
    port = free_port()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PAPIQ_")}
    environment |= {
        "PAPIQ_DB_SQLITE_PATH": str(tmp_path / "papiq.db"),
        "PAPIQ_STORAGE_PATH": str(tmp_path / "objects"),
        "PAPIQ_API_HOST": "127.0.0.1",
        "PAPIQ_API_PORT": str(port),
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
        process.send_signal(signal.SIGTERM)
        output = process.stderr.read()
        assert process.wait(timeout=30) == 0, output
    assert health == {"status": "ok", "checks": {"database": "ok", "object_store": "ok"}}

import json
import sqlite3
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

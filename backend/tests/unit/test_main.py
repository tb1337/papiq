import json
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

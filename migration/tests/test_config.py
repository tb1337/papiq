from pathlib import Path

import pytest

from papiq_migration.config import Config, ConfigError, secret


def load(env: dict[str, str], **changes: object) -> Config:
    values: dict[str, object] = {
        "paperless_url": None,
        "papiq_url": None,
        "state": Path("s"),
        "report_dir": Path("r"),
        "currency": "eur",
        "concurrency": 2,
        "limit": None,
        "pipeline_timeout": 10.0,
    }
    return Config.load(**{**values, **changes}, env=env)  # type: ignore[arg-type]


def test_the_environment_configures_everything() -> None:
    config = load(
        {
            "PAPIQ_MIGRATION_PAPERLESS_URL": "http://p/",
            "PAPIQ_MIGRATION_PAPERLESS_TOKEN": " key ",
            "PAPIQ_MIGRATION_PAPIQ_URL": "http://q",
            "PAPIQ_MIGRATION_PAPIQ_TOKEN": "other",
        }
    )
    assert (config.paperless_url, config.papiq_url, config.currency) == (
        "http://p",
        "http://q",
        "EUR",
    )
    assert (config.paperless_token, config.papiq_token) == ("key", "other")


def test_keys_never_show_in_the_text_of_the_configuration() -> None:
    config = load(
        {
            "PAPIQ_MIGRATION_PAPERLESS_URL": "http://p",
            "PAPIQ_MIGRATION_PAPERLESS_TOKEN": "very-secret",
        }
    )
    assert "very-secret" not in repr(config) and "very-secret" not in str(config)


def test_a_key_may_come_from_a_file(tmp_path: Path) -> None:
    file = tmp_path / "key"
    file.write_text("from-file\n")
    assert secret({"PAPIQ_MIGRATION_X_FILE": str(file)}, "X") == "from-file"
    with pytest.raises(ConfigError, match="only one"):
        secret({"PAPIQ_MIGRATION_X_FILE": str(file), "PAPIQ_MIGRATION_X": "v"}, "X")
    with pytest.raises(ConfigError, match="cannot read"):
        secret({"PAPIQ_MIGRATION_X_FILE": str(tmp_path / "missing")}, "X")


@pytest.mark.parametrize(
    ("env", "changes", "text"),
    [
        ({}, {}, "Paperless URL"),
        ({"PAPIQ_MIGRATION_PAPERLESS_URL": "http://p"}, {}, "API key"),
        (
            {"PAPIQ_MIGRATION_PAPERLESS_URL": "http://p", "PAPIQ_MIGRATION_PAPERLESS_TOKEN": "k"},
            {"currency": "euro"},
            "currency",
        ),
        (
            {"PAPIQ_MIGRATION_PAPERLESS_URL": "http://p", "PAPIQ_MIGRATION_PAPERLESS_TOKEN": "k"},
            {"concurrency": 0},
            "concurrency",
        ),
    ],
)
def test_missing_or_wrong_settings_are_refused(
    env: dict[str, str], changes: dict[str, object], text: str
) -> None:
    with pytest.raises(ConfigError, match=text):
        load(env, **changes)

from pathlib import Path

import pytest

from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings, find_unknown_variables, load_settings

POSTGRES = {
    "PAPIQ_DB_TYPE": "postgres",
    "PAPIQ_DB_HOST": "db",
    "PAPIQ_DB_NAME": "papiq",
    "PAPIQ_DB_USER": "papiq",
    "PAPIQ_DB_PASSWORD": "pw",
}
S3 = {
    "PAPIQ_STORAGE_TYPE": "s3",
    "PAPIQ_S3_ENDPOINT_URL": "http://garage:3900",
    "PAPIQ_S3_BUCKET": "papiq",
    "PAPIQ_S3_ACCESS_KEY_ID": "key",
    "PAPIQ_S3_SECRET_ACCESS_KEY": "secret",
}


def set_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def error_message(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> str:
    set_env(monkeypatch, values)
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    return str(info.value)


def test_defaults() -> None:
    settings = load_settings()
    assert settings.role == "all"
    assert settings.log_format == "json"
    assert settings.log_level == "INFO"
    assert settings.db_type == "sqlite"
    assert settings.storage_type == "filesystem"
    assert settings.meilisearch_url is None
    assert settings.llm_base_url is None
    assert settings.embedding_base_url is None


def test_valid_postgres_and_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, POSTGRES | S3 | {"PAPIQ_DB_PORT": "6543", "PAPIQ_ROLE": "worker"})
    settings = load_settings()
    assert settings.db_type == "postgres"
    assert settings.db_port == 6543
    assert settings.db_password is not None
    assert settings.db_password.get_secret_value() == "pw"
    assert settings.storage_type == "s3"
    assert str(settings.s3_endpoint_url) == "http://garage:3900/"
    assert settings.role == "worker"


def test_llm_and_embedding_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(
        monkeypatch,
        {
            "PAPIQ_LLM_BASE_URL": "http://host.docker.internal:11434/v1",
            "PAPIQ_LLM_MODEL": "qwen3",
            "PAPIQ_EMBEDDING_BASE_URL": "http://localhost:11434/v1",
            "PAPIQ_EMBEDDING_MODEL": "bge-m3",
        },
    )
    settings = load_settings()
    assert settings.llm_model == "qwen3"
    assert settings.embedding_model == "bge-m3"


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, {"PAPIQ_LOG_LEVEL": "debug"})
    assert load_settings().log_level == "DEBUG"


def test_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, {"PAPIQ_DB_TYPE": ""})
    assert load_settings().db_type == "sqlite"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PAPIQ_ROLE", "everything"),
        ("PAPIQ_DB_TYPE", "mysql"),
        ("PAPIQ_STORAGE_TYPE", "ftp"),
        ("PAPIQ_LOG_FORMAT", "xml"),
        ("PAPIQ_LOG_LEVEL", "LOUD"),
        ("PAPIQ_DB_PORT", "0"),
        ("PAPIQ_DB_PORT", "abc"),
        ("PAPIQ_MEILISEARCH_URL", "not a url"),
    ],
)
def test_invalid_value_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    message = error_message(monkeypatch, {variable: value})
    assert f"{variable}:" in message
    assert value in message


def test_postgres_requires_connection_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    message = error_message(monkeypatch, {"PAPIQ_DB_TYPE": "postgres"})
    for name in ("HOST", "NAME", "USER", "PASSWORD"):
        assert f"PAPIQ_DB_{name} is required when PAPIQ_DB_TYPE=postgres" in message


def test_s3_requires_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    message = error_message(monkeypatch, {"PAPIQ_STORAGE_TYPE": "s3"})
    for name in ("ENDPOINT_URL", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        assert f"PAPIQ_S3_{name} is required when PAPIQ_STORAGE_TYPE=s3" in message


def test_sqlite_ignores_postgres_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, {"PAPIQ_DB_HOST": "db"})
    assert load_settings().db_type == "sqlite"


@pytest.mark.parametrize("prefix", ["LLM", "EMBEDDING"])
def test_endpoint_and_model_must_be_set_together(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    message = error_message(monkeypatch, {f"PAPIQ_{prefix}_MODEL": "m"})
    assert f"PAPIQ_{prefix}_BASE_URL and PAPIQ_{prefix}_MODEL must be set together" in message


def test_all_problems_are_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    message = error_message(monkeypatch, {"PAPIQ_ROLE": "x", "PAPIQ_DB_PORT": "0"})
    assert "PAPIQ_ROLE" in message
    assert "PAPIQ_DB_PORT" in message


def test_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, POSTGRES)
    settings = load_settings()
    assert "pw" not in repr(settings).replace("papiq", "")
    assert settings.describe()["db_password"] == "**********"


def test_secret_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "db"
    secret.write_text("from-file\n")
    set_env(monkeypatch, POSTGRES)
    monkeypatch.delenv("PAPIQ_DB_PASSWORD")
    monkeypatch.setenv("PAPIQ_DB_PASSWORD_FILE", str(secret))
    settings = load_settings()
    assert settings.db_password is not None
    assert settings.db_password.get_secret_value() == "from-file"


def test_only_one_trailing_newline_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "key"
    secret.write_text(" spaced \n\n")
    monkeypatch.setenv("PAPIQ_MEILISEARCH_API_KEY_FILE", str(secret))
    settings = load_settings()
    assert settings.meilisearch_api_key is not None
    assert settings.meilisearch_api_key.get_secret_value() == " spaced \n"


def test_variable_and_file_together_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "db"
    secret.write_text("from-file")
    message = error_message(
        monkeypatch,
        {"PAPIQ_DB_PASSWORD": "pw", "PAPIQ_DB_PASSWORD_FILE": str(secret)},
    )
    assert message == (
        "PAPIQ_DB_PASSWORD and PAPIQ_DB_PASSWORD_FILE are both set; use only one of them"
    )


def test_missing_secret_file_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    message = error_message(monkeypatch, {"PAPIQ_LLM_API_KEY_FILE": str(missing)})
    assert "PAPIQ_LLM_API_KEY_FILE" in message
    assert str(missing) in message


def test_empty_secret_file_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "empty"
    secret.write_text("\n")
    message = error_message(monkeypatch, {"PAPIQ_LLM_API_KEY_FILE": str(secret)})
    assert "is empty" in message


def test_file_variant_exists_only_for_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    role = tmp_path / "role"
    role.write_text("api")
    set_env(monkeypatch, {"PAPIQ_ROLE_FILE": str(role)})
    assert Settings().role == "all"
    assert find_unknown_variables() == ["PAPIQ_ROLE_FILE"]


def test_unknown_variables_are_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, {"PAPIQ_DB_TYPO": "x", "PAPIQ_ROLE": "all", "UNRELATED": "y"})
    assert find_unknown_variables() == ["PAPIQ_DB_TYPO"]

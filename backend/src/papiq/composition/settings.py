"""Configuration: environment variables prefixed `PAPIQ_`, no configuration file.

Secrets can also be passed as `PAPIQ_<NAME>_FILE` (Docker secrets): the file content is the value.
Setting both `PAPIQ_<NAME>` and `PAPIQ_<NAME>_FILE` is an error.
"""

import os
import re
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AnyHttpUrl,
    BeforeValidator,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from papiq.composition.errors import ConfigurationError

ENV_PREFIX = "PAPIQ_"
FILE_SUFFIX = "_FILE"

# Choices are written in lower case; `PAPIQ_ROLE=API` is accepted.
_LOWERCASE_FIELDS = {"role", "log_format", "db_type", "storage_type"}
# Credentials in URLs (`http://user:password@host`) must not reach logs or error messages.
_URL_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")

LogLevel = Annotated[
    Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    BeforeValidator(lambda value: value.upper() if isinstance(value, str) else value),
]


_NUMBER = re.compile(r"\d+(\.\d+)?")


def _seconds(value: Any) -> Any:
    """Durations are seconds (`30`, `1.5`); ISO 8601 (`PT1H`, `P7D`) works too."""
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        return float(value)
    return value


# A positive duration, configured in seconds.
Seconds = Annotated[timedelta, BeforeValidator(_seconds), Field(gt=timedelta(0))]


class Settings(BaseSettings):
    """All technical settings. Business data (users, drawers, rules, ...) is not configuration."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_ignore_empty=True,
        extra="ignore",
    )

    # Which services run in this container (read by the s6 init scripts, see M9).
    role: Literal["all", "api", "worker"] = "all"

    log_format: Literal["console", "json"] = "json"
    log_level: LogLevel = "INFO"

    # Database: SQLite or Postgres.
    db_type: Literal["sqlite", "postgres"] = "sqlite"
    db_sqlite_path: Path = Path("data/papiq.db")
    db_host: str | None = None
    db_port: Annotated[int, Field(ge=1, le=65535)] = 5432
    db_name: str | None = None
    db_user: str | None = None
    db_password: SecretStr | None = None

    # Object store: local filesystem or S3 (first server: Garage).
    storage_type: Literal["filesystem", "s3"] = "filesystem"
    storage_path: Path = Path("data/objects")
    s3_endpoint_url: AnyHttpUrl | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_path_style: bool = True

    # Worker: background jobs, event delivery and cleanup.
    worker_concurrency: Annotated[int, Field(ge=1, le=64)] = 2
    worker_poll_interval: Seconds = timedelta(seconds=1)
    worker_shutdown_timeout: Seconds = timedelta(seconds=30)
    step_max_attempts: Annotated[int, Field(ge=1, le=20)] = 3
    step_retry_delay: Seconds = timedelta(seconds=30)
    events_poll_interval: Seconds = timedelta(seconds=1)
    events_max_attempts: Annotated[int, Field(ge=1, le=100)] = 10
    cleanup_interval: Seconds = timedelta(hours=1)
    retention: Seconds = timedelta(days=7)

    # Processing: OCR (OCRmyPDF, Tesseract language codes joined by `+`) and parsing (Docling).
    ocr_languages: Annotated[str, Field(pattern=r"^[a-z_]+(\+[a-z_]+)*$")] = "deu+eng"
    ocr_timeout: Seconds = timedelta(minutes=10)
    parse_timeout: Seconds = timedelta(minutes=10)
    docling_models_path: Path = Path("/opt/docling-models")

    # Search (required from M6 on).
    meilisearch_url: AnyHttpUrl | None = None
    meilisearch_api_key: SecretStr | None = None

    # Language model and embeddings: OpenAI-compatible endpoints (Ollama, cloud).
    llm_base_url: AnyHttpUrl | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    embedding_base_url: AnyHttpUrl | None = None
    embedding_model: str | None = None
    embedding_api_key: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        """Strip whitespace, treat blank values as unset, lower-case the choices."""
        if not isinstance(data, dict):
            return data
        values: dict[str, Any] = {}
        for name, value in data.items():
            if isinstance(value, str):
                if not value.strip():
                    continue
                if name in _LOWERCASE_FIELDS:
                    value = value.strip().lower()
                elif name not in _secret_fields(cls):
                    value = value.strip()
            values[name] = value
        return values

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        problems: list[str] = []
        if self.db_type == "postgres":
            problems += _missing(self, "db_type", "db_host", "db_name", "db_user", "db_password")
        if self.storage_type == "s3":
            problems += _missing(
                self,
                "storage_type",
                "s3_endpoint_url",
                "s3_bucket",
                "s3_access_key_id",
                "s3_secret_access_key",
            )
        for prefix in ("llm", "embedding"):
            url, model = f"{prefix}_base_url", f"{prefix}_model"
            if (getattr(self, url) is None) != (getattr(self, model) is None):
                problems.append(f"{_env(url)} and {_env(model)} must be set together")
        if problems:
            raise ValueError("\n".join(problems))
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, _SecretFileSource(settings_cls))

    def describe(self) -> dict[str, Any]:
        """The effective configuration for logging; secrets are masked."""
        return {
            name: _mask_userinfo(value) if isinstance(value, str) else value
            for name, value in self.model_dump(mode="json").items()
        }


class _SecretFileSource(PydanticBaseSettingsSource):
    """Reads secrets from the file named by `PAPIQ_<NAME>_FILE`."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError  # unused: __call__ handles all fields at once

    def __call__(self) -> dict[str, Any]:
        environ = {key.upper(): value for key, value in os.environ.items() if value}
        values: dict[str, Any] = {}
        for name in _secret_fields(self.settings_cls):
            variable = _env(name)
            file_variable = variable + FILE_SUFFIX
            path = environ.get(file_variable)
            if path is None:
                continue
            if variable in environ:
                raise ConfigurationError(
                    f"{variable} and {file_variable} are both set; use only one of them"
                )
            values[name] = _read_secret(file_variable, path)
        return values


def _read_secret(variable: str, path: str) -> str:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"{variable}: cannot read '{path}': {error.strerror or error}"
        ) from error
    except UnicodeDecodeError as error:
        raise ConfigurationError(f"{variable}: '{path}' is not valid UTF-8 text") from error
    value = content.removesuffix("\n").removesuffix("\r")
    if not value.strip():
        raise ConfigurationError(f"{variable}: '{path}' is empty")
    return value


def _secret_fields(settings_cls: type[BaseSettings]) -> list[str]:
    return [
        name
        for name, field in settings_cls.model_fields.items()
        if SecretStr in get_args(field.annotation)
    ]


def _mask_userinfo(text: str) -> str:
    return _URL_USERINFO.sub("***@", text)


def _env(field_name: str) -> str:
    return ENV_PREFIX + field_name.upper()


def _missing(settings: Settings, selector: str, *names: str) -> list[str]:
    value = getattr(settings, selector)
    return [
        f"{_env(name)} is required when {_env(selector)}={value}"
        for name in names
        if getattr(settings, name) is None
    ]


def find_unknown_variables(environ: Mapping[str, str] | None = None) -> list[str]:
    """`PAPIQ_` variables that match no setting; usually typos."""
    environ = os.environ if environ is None else environ
    secrets = {_env(name) + FILE_SUFFIX for name in _secret_fields(Settings)}
    known = {_env(name) for name in Settings.model_fields} | secrets
    return sorted(
        key for key in environ if key.upper().startswith(ENV_PREFIX) and key.upper() not in known
    )


def load_settings() -> Settings:
    """Read and validate the configuration from the environment.

    Raises ConfigurationError with a message that names the offending variables.
    """
    try:
        return Settings()
    except ValidationError as error:
        raise ConfigurationError(_format(error)) from error


def _format(error: ValidationError) -> str:
    secrets = set(_secret_fields(Settings))
    lines: list[str] = []
    for item in error.errors():
        location = item["loc"]
        if not location:
            # Raised by the consistency check: the message already names the variables.
            lines += item["msg"].removeprefix("Value error, ").splitlines()
            continue
        name = str(location[0])
        line = f"{_env(name)}: {item['msg']}"
        if name not in secrets:
            line += f" (got {_mask_userinfo(repr(item['input']))})"
        lines.append(line)
    return "Invalid configuration:\n" + "\n".join(f"  - {line}" for line in lines)

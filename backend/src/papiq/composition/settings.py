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
    ByteSize,
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

from papiq.adapters.outbound.crypto import decode_key
from papiq.composition.errors import ConfigurationError

ENV_PREFIX = "PAPIQ_"
FILE_SUFFIX = "_FILE"

# The secret key of the devcontainer (`.devcontainer/dev.env`); public, so never for production.
DEVELOPMENT_SECRET_KEY = "ZGV2LWtleS1kZXYta2V5LWRldi1rZXktZGV2LWtleS0="

# Choices are written in lower case; `PAPIQ_ROLE=API` is accepted.
_LOWERCASE_FIELDS = {"role", "log_format", "db_type", "storage_type", "llm_response_format"}
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

    # API (Uvicorn).
    api_host: str = "0.0.0.0"
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    upload_max_size: Annotated[ByteSize, Field(gt=0)] = ByteSize(100 * 1024 * 1024)
    # Every other request body (JSON).
    request_max_size: Annotated[ByteSize, Field(gt=0)] = ByteSize(1024 * 1024)
    # Addresses of reverse proxies whose X-Forwarded-For is trusted (comma-separated, `*` for
    # all); the client address counts failed sign-ins per source. Required with secure cookies.
    forwarded_allow_ips: str | None = None

    # Identity. The secret key (32 bytes, base64) encrypts TOTP secrets; required for the API.
    secret_key: SecretStr | None = None
    # The first admin: created at API start while there is no admin at all.
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    session_idle_timeout: Seconds = timedelta(days=1)
    session_max_age: Seconds = timedelta(days=30)
    # `false` only for development over plain HTTP: the session cookie loses `Secure`.
    cookie_secure: bool = True
    # Where browsers reach Papiq (e.g. `https://papiq.example.org`); needed for OIDC.
    public_url: AnyHttpUrl | None = None

    # OpenID Connect (optional): one provider, Authorization Code Flow with PKCE.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_display_name: str = "Single sign-on"
    # Create a local account at the first sign-in of an unknown provider account.
    oidc_auto_create: bool = False
    # The ID token claim that names a new account.
    oidc_username_claim: str = "preferred_username"

    # Webhooks: how many a user may have; how long a renewed secret's predecessor still signs.
    webhooks_per_user: Annotated[int, Field(ge=1, le=1000)] = 20
    webhook_secret_grace: Seconds = timedelta(hours=24)
    # Delivery (worker): time per attempt, attempts per event with a doubling delay (up to one
    # hour), failed deliveries in a row until a webhook is switched off, parallel deliveries.
    webhook_timeout: Annotated[Seconds, Field(ge=timedelta(seconds=1), le=timedelta(minutes=2))] = (
        timedelta(seconds=10)
    )
    webhook_max_attempts: Annotated[int, Field(ge=1, le=50)] = 10
    webhook_retry_delay: Seconds = timedelta(seconds=30)
    webhook_disable_after: Annotated[int, Field(ge=1, le=10_000)] = 20
    webhook_concurrency: Annotated[int, Field(ge=1, le=64)] = 4

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

    # Search: Meilisearch holds the index of full text and vectors.
    meilisearch_url: AnyHttpUrl | None = None
    meilisearch_api_key: SecretStr | None = None
    # The active index; a rebuild fills `<name>-rebuild` and swaps it in.
    meilisearch_index: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")] = "papiq-documents"
    meilisearch_timeout: Seconds = timedelta(seconds=30)
    # How long a write waits for its task (indexing a batch of documents).
    meilisearch_task_timeout: Seconds = timedelta(minutes=2)
    # Languages of the documents (ISO 639-3 codes joined by `+`), for tokenising.
    search_locales: Annotated[str, Field(pattern=r"^[a-z]{3}(\+[a-z]{3})*$")] = "deu+eng"
    # Hybrid search: the weight of the meaning against the words (0: words only, 1: meaning
    # only) when a request does not say; the wait for the embedding of a query, after which
    # the search falls back to the words.
    search_semantic_ratio: Annotated[float, Field(ge=0, le=1)] = 0.5
    search_embed_timeout: Seconds = timedelta(seconds=5)
    # Indexing: characters of text per document; the text is cut into sections of about
    # `search_chunk_size` characters that get a vector each, at most `search_max_chunks`.
    search_max_text: Annotated[int, Field(ge=1000, le=10_000_000)] = 200_000
    search_chunk_size: Annotated[int, Field(ge=100, le=100_000)] = 1500
    search_max_chunks: Annotated[int, Field(ge=1, le=100)] = 8
    # The index is compared with the database this often; a rebuild may take this long.
    search_reconcile_interval: Seconds = timedelta(hours=6)
    search_rebuild_timeout: Seconds = timedelta(hours=6)

    # Language model and embeddings: OpenAI-compatible endpoints (Ollama, cloud).
    # The base URL includes the version path, e.g. `http://ollama:11434/v1`.
    llm_base_url: AnyHttpUrl | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_temperature: Annotated[float, Field(ge=0, le=2)] = 0.0
    llm_seed: int | None = None
    llm_timeout: Seconds = timedelta(minutes=5)
    # `json_object` for providers without JSON Schema support; the answer is checked anyway.
    llm_response_format: Literal["json_schema", "json_object"] = "json_schema"
    # Characters of document text in a prompt; longer documents are shortened.
    llm_input_budget: Annotated[int, Field(ge=1000, le=1_000_000)] = 12_000
    # Tags listed in a prompt; with more, those named in the text come first.
    llm_max_tags: Annotated[int, Field(ge=1, le=10_000)] = 200
    embedding_base_url: AnyHttpUrl | None = None
    embedding_model: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_timeout: Seconds = timedelta(minutes=1)
    # Length of the vectors (`bge-m3`: 1024); the search index needs it to be set up.
    embedding_dimensions: Annotated[int, Field(ge=1, le=65535)] | None = None
    # Some models want a hint in front of the text (e5: `query:` and `passage:`); a space
    # separates it from the text.
    embedding_query_prefix: str | None = None
    embedding_document_prefix: str | None = None

    # Classification: a field is accepted (ok) from this confidence on; an existing contact is
    # suggested from the second.
    confidence_threshold: Annotated[float, Field(gt=0, le=1)] = 0.9
    contact_suggest_threshold: Annotated[float, Field(gt=0, le=1)] = 0.75

    # Rules: the time limit of one regular expression, the characters of document text that
    # text conditions see, and the documents one retroactive application may cover.
    rules_pattern_timeout: Seconds = timedelta(milliseconds=200)
    rules_max_text: Annotated[int, Field(ge=1000, le=10_000_000)] = 200_000
    rules_apply_max_documents: Annotated[int, Field(ge=1, le=100_000)] = 1000

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
        if self.role in ("all", "api") and self.cookie_secure and self.forwarded_allow_ips is None:
            problems.append(
                f"{_env('forwarded_allow_ips')} is required with {_env('cookie_secure')}=true: "
                "secure cookies need a TLS-terminating proxy in front of Papiq; name its address "
                "(`*` only if the proxy sets X-Forwarded-For itself, replacing what clients "
                f"send). For development over plain HTTP set {_env('cookie_secure')}=false"
            )
        if self.secret_key is None:
            # The API encrypts TOTP and webhook secrets, the worker decrypts the latter to sign.
            problems.append(f"{_env('secret_key')} is required")
        if self.secret_key is not None:
            try:
                key = decode_key(self.secret_key.get_secret_value())
            except ValueError as error:
                problems.append(
                    f"{_env('secret_key')}: {error}; create one with `openssl rand -base64 32`"
                )
            else:
                if self.cookie_secure and key == decode_key(DEVELOPMENT_SECRET_KEY):
                    problems.append(
                        f"{_env('secret_key')} is the development key from the repository; "
                        "create one with `openssl rand -base64 32`"
                    )
        if (self.admin_username is None) != (self.admin_password is None):
            problems.append(
                f"{_env('admin_username')} and {_env('admin_password')} must be set together"
            )
        if self.session_max_age < self.session_idle_timeout:
            problems.append(
                f"{_env('session_max_age')} must not be shorter than {_env('session_idle_timeout')}"
            )
        problems += self._oidc_problems()
        for prefix in ("llm", "embedding"):
            url, model = f"{prefix}_base_url", f"{prefix}_model"
            if (getattr(self, url) is None) != (getattr(self, model) is None):
                problems.append(f"{_env(url)} and {_env(model)} must be set together")
        if (
            self.meilisearch_url is not None
            and self.embedding_base_url is not None
            and self.embedding_dimensions is None
        ):
            problems.append(
                f"{_env('embedding_dimensions')} is required with {_env('meilisearch_url')} and "
                f"{_env('embedding_base_url')}: the search index is set up for vectors of that "
                "length (`bge-m3`: 1024)"
            )
        if self.contact_suggest_threshold > self.confidence_threshold:
            problems.append(
                f"{_env('contact_suggest_threshold')} must not be above "
                f"{_env('confidence_threshold')}"
            )
        if problems:
            # Not a ValueError: pydantic would wrap it; the message is complete already.
            raise ConfigurationError(_report(problems))
        return self

    @property
    def oidc_enabled(self) -> bool:
        return self.oidc_issuer is not None

    def _oidc_problems(self) -> list[str]:
        names = ("oidc_issuer", "oidc_client_id", "oidc_client_secret")
        given = [getattr(self, name) is not None for name in names]
        if not any(given):
            return []
        if not all(given):
            return [f"{', '.join(_env(name) for name in names)} must be set together"]
        problems = []
        assert self.oidc_issuer is not None
        if not re.fullmatch(r"https://[^\s/?#]+(/[^\s?#]*)?", self.oidc_issuer):
            problems.append(f"{_env('oidc_issuer')}: must be an https URL without query")
        if "openid" not in self.oidc_scopes.split():
            problems.append(f"{_env('oidc_scopes')}: must contain 'openid'")
        if self.public_url is None:
            problems.append(f"{_env('public_url')} is required with OIDC (for the redirect URI)")
        return problems

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


def _report(lines: list[str]) -> str:
    return "Invalid configuration:\n" + "\n".join(f"  - {line}" for line in lines)


def _format(error: ValidationError) -> str:
    secrets = set(_secret_fields(Settings))
    lines: list[str] = []
    for item in error.errors():
        name = str(item["loc"][0])
        line = f"{_env(name)}: {item['msg']}"
        if name not in secrets:
            line += f" (got {_mask_userinfo(repr(item['input']))})"
        lines.append(line)
    return _report(lines)

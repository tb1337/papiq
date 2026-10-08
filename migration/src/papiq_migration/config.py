"""Configuration: environment variables (`PAPIQ_MIGRATION_*`) and command-line options. The API
keys come from the environment or a file, never from an option, and are never written anywhere."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "PAPIQ_MIGRATION_"


class ConfigError(Exception):
    pass


def secret(env: Mapping[str, str], name: str) -> str | None:
    """`PAPIQ_MIGRATION_<NAME>`, or the content of the file `PAPIQ_MIGRATION_<NAME>_FILE`; both
    set is an error."""
    value, file = env.get(PREFIX + name), env.get(f"{PREFIX}{name}_FILE")
    if value and file:
        raise ConfigError(f"set only one of {PREFIX}{name} and {PREFIX}{name}_FILE")
    if file:
        try:
            return Path(file).read_text(encoding="utf-8").strip() or None
        except OSError as error:
            raise ConfigError(f"cannot read {PREFIX}{name}_FILE: {error.strerror}") from None
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class Config:
    paperless_url: str
    papiq_url: str | None
    state: Path
    report_dir: Path
    paperless_token: str = field(repr=False, default="")
    papiq_token: str | None = field(repr=False, default=None)
    currency: str = "EUR"
    concurrency: int = 4
    limit: int | None = None
    pipeline_timeout: float = 3600.0
    rehash: bool = False

    @classmethod
    def load(
        cls,
        *,
        paperless_url: str | None,
        papiq_url: str | None,
        state: Path,
        report_dir: Path,
        currency: str,
        concurrency: int,
        limit: int | None,
        pipeline_timeout: float,
        rehash: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> "Config":
        env = os.environ if env is None else env
        paperless = paperless_url or env.get(PREFIX + "PAPERLESS_URL")
        papiq = papiq_url or env.get(PREFIX + "PAPIQ_URL")
        if not paperless:
            raise ConfigError(
                f"the Paperless URL is missing ({PREFIX}PAPERLESS_URL or --paperless-url)"
            )
        token = secret(env, "PAPERLESS_TOKEN")
        if not token:
            raise ConfigError(f"the Paperless API key is missing ({PREFIX}PAPERLESS_TOKEN)")
        if len(currency) != 3 or not currency.isalpha():
            raise ConfigError("the currency is an ISO 4217 code such as EUR")
        if concurrency < 1:
            raise ConfigError("the concurrency is at least 1")
        return cls(
            paperless_url=paperless.rstrip("/"),
            papiq_url=papiq.rstrip("/") if papiq else None,
            state=state,
            report_dir=report_dir,
            paperless_token=token,
            papiq_token=secret(env, "PAPIQ_TOKEN"),
            currency=currency.upper(),
            concurrency=concurrency,
            limit=limit,
            pipeline_timeout=pipeline_timeout,
            rehash=rehash,
        )

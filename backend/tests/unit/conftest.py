import logging
import os
from collections.abc import Iterator

import pytest
import structlog

from tests.builders import SECRET_KEY, TRUSTED_PROXY


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process environment (devcontainer, CI) must not leak into unit tests."""
    for name in list(os.environ):
        if name.upper().startswith("PAPIQ_"):
            monkeypatch.delenv(name)
    # Required for the API; tests that check the requirement remove it.
    monkeypatch.setenv("PAPIQ_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("PAPIQ_FORWARDED_ALLOW_IPS", TRUSTED_PROXY)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level
    structlog.reset_defaults()

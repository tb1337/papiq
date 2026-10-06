import logging
import os
from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process environment (devcontainer, CI) must not leak into unit tests."""
    for name in list(os.environ):
        if name.upper().startswith("PAPIQ_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level
    structlog.reset_defaults()

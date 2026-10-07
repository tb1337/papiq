"""The first admin from configuration, at API start."""

import pytest
from pydantic import SecretStr

from papiq.adapters.outbound.memory import ManualClock
from papiq.composition.bootstrap import ensure_first_admin
from papiq.composition.container import build_memory_container, build_services
from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings
from tests import builders
from tests.builders import PASSWORD


def settings(username: str | None, password: str | None) -> Settings:
    return Settings(
        secret_key=SecretStr(builders.SECRET_KEY),
        admin_username=username,
        admin_password=None if password is None else SecretStr(password),
    )


async def test_creates_the_admin_once() -> None:
    services = build_services(build_memory_container(ManualClock(builders.NOW)))
    await ensure_first_admin(services.users, settings("root", PASSWORD))
    await ensure_first_admin(services.users, settings("root", "a different password"))
    await services.auth.login("root", PASSWORD)


async def test_without_configuration_nothing_happens() -> None:
    services = build_services(build_memory_container(ManualClock(builders.NOW)))
    await ensure_first_admin(services.users, settings(None, None))


async def test_problems_are_configuration_errors() -> None:
    services = build_services(build_memory_container(ManualClock(builders.NOW)))
    with pytest.raises(ConfigurationError, match="PAPIQ_ADMIN_PASSWORD"):
        await ensure_first_admin(services.users, settings("root", "short"))

import pytest

from papiq.composition.settings import Settings, load_settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The configuration from the environment: in the devcontainer, the compose services."""
    return load_settings()

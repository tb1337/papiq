"""Errors raised while starting up: invalid configuration, missing adapters."""


class ConfigurationError(Exception):
    """The configuration is invalid or contradictory. The message is meant for operators."""


class AdapterNotAvailableError(Exception):
    """The configured adapter for a port does not exist (yet)."""

    def __init__(self, port: str, adapter: str) -> None:
        super().__init__(f"{port}: adapter '{adapter}' is not implemented yet")
        self.port = port
        self.adapter = adapter

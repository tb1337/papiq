from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """The current time. A port so that tests control time."""

    def now(self) -> datetime:
        """The current time, timezone-aware in UTC."""
        ...

from datetime import UTC, datetime


class SystemClock:
    """The real time, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)

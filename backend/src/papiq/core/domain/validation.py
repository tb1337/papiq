"""Small checks shared by the domain model."""

from datetime import UTC, datetime, timedelta

from papiq.core.domain.errors import ValidationError


def require_utc(value: datetime, what: str = "timestamp") -> datetime:
    """Timestamps are timezone-aware and in UTC; naive or offset times are rejected."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValidationError(f"{what} must be timezone-aware UTC, got {value.isoformat()}")
    return value.astimezone(UTC)


def require_name(value: str, what: str = "name") -> str:
    """Names are stripped and must not be empty."""
    name = value.strip()
    if not name:
        raise ValidationError(f"{what} must not be empty")
    return name


def name_key(name: str) -> str:
    """Comparison key for names that are unique regardless of case."""
    return name.strip().casefold()

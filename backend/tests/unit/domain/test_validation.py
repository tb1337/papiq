from datetime import UTC, datetime, timedelta, timezone

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.validation import name_key, require_name, require_utc


def test_require_utc_accepts_utc() -> None:
    value = datetime(2026, 1, 1, tzinfo=UTC)
    assert require_utc(value) == value


def test_require_utc_accepts_zero_offset_and_normalises() -> None:
    value = datetime(2026, 1, 1, tzinfo=timezone(timedelta(0)))
    assert require_utc(value).tzinfo is UTC


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))],
)
def test_require_utc_rejects_naive_and_offset_times(value: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        require_utc(value)


def test_require_name_strips_and_rejects_blank() -> None:
    assert require_name("  Invoice ") == "Invoice"
    with pytest.raises(ValidationError, match="title must not be empty"):
        require_name("   ", "title")


def test_name_key_ignores_case_and_surrounding_space() -> None:
    assert name_key(" Straße ") == name_key("STRASSE")

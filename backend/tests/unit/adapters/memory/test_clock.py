from datetime import UTC, datetime, timedelta

import pytest

from papiq.adapters.outbound.memory import ManualClock
from papiq.core.domain.errors import ValidationError

START = datetime(2026, 10, 6, tzinfo=UTC)


def test_manual_clock_moves_only_when_told() -> None:
    clock = ManualClock(START)
    assert clock.now() == START
    clock.advance(timedelta(minutes=5))
    assert clock.now() == START + timedelta(minutes=5)
    clock.set(START)
    assert clock.now() == START


def test_manual_clock_rejects_invalid_times() -> None:
    with pytest.raises(ValidationError):
        ManualClock(datetime(2026, 10, 6))
    with pytest.raises(ValueError, match="backwards"):
        ManualClock(START).advance(timedelta(seconds=-1))

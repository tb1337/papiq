import pytest

from papiq.adapters.outbound.system import SystemClock
from papiq.core.ports import Clock
from tests.contracts.clock import ClockContract


@pytest.fixture
def clock() -> Clock:
    return SystemClock()


class TestSystemClock(ClockContract):
    pass

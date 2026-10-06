import time
from uuid import UUID

from papiq.core.domain.ids import new_id


def test_new_id_is_a_uuid_version_7() -> None:
    value = new_id()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_new_id_carries_the_current_time_in_milliseconds() -> None:
    before = time.time_ns() // 1_000_000
    value = new_id()
    after = time.time_ns() // 1_000_000
    assert before <= value.int >> 80 <= after


def test_new_ids_are_unique_and_ordered_by_millisecond() -> None:
    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first != second
    assert first < second
    assert isinstance(first, UUID)

import itertools

import pytest

from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.permissions import can_read_document
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.search import (
    PROCESSING,
    EmbeddingStamp,
    Visibility,
    lane_value,
)
from tests import builders

LANES = [None, Lane.GREEN, Lane.YELLOW, Lane.RED]


def test_lane_values() -> None:
    assert [lane_value(lane) for lane in LANES] == [PROCESSING, "green", "yellow", "red"]


@pytest.mark.parametrize(
    ("lane", "reader_is_owner", "drawer_owner_is_reader", "share"),
    list(itertools.product(LANES, [False, True], [False, True], [None, *ShareLevel])),
)
def test_visibility_is_the_read_rule_of_permissions(
    lane: Lane | None,
    reader_is_owner: bool,
    drawer_owner_is_reader: bool,
    share: ShareLevel | None,
) -> None:
    """`Visibility.allows` and `can_read_document` agree for every combination of lane,
    ownership of the document and of the drawer, and share."""
    reader = builders.user("reader")
    other = builders.user("other")
    drawer = builders.drawer(reader if drawer_owner_is_reader else other)
    if share is not None and not drawer_owner_is_reader:
        drawer.share(reader.id, share)
    document = builders.document(reader if reader_is_owner else other, drawer)
    document.lane = lane

    accessible = frozenset({drawer.id}) if drawer.owner_id == reader.id or share else frozenset()
    index_document = builders.index_document(
        id=document.id, owner_id=document.owner_id, drawer_id=drawer.id, lane=lane
    )

    assert Visibility(reader.id, accessible).allows(index_document) == can_read_document(
        reader, document, drawer
    )


def test_no_drawers_leave_only_own_documents() -> None:
    me = builders.user()
    visibility = Visibility(me.id, frozenset())
    assert visibility.allows(builders.index_document(owner_id=me.id, lane=None))
    assert not visibility.allows(builders.index_document())


def test_vectors_need_a_stamp_and_one_length() -> None:
    stamp = EmbeddingStamp("model", "digest")
    builders.index_document(vectors=((1.0, 2.0), (3.0, 4.0)), embedding=stamp)
    with pytest.raises(ValueError, match="stamp"):
        builders.index_document(vectors=((1.0,),))
    with pytest.raises(ValueError, match="one length"):
        builders.index_document(vectors=((1.0,), (1.0, 2.0)), embedding=stamp)
    with pytest.raises(ValueError, match="one length"):
        builders.index_document(vectors=((),), embedding=stamp)

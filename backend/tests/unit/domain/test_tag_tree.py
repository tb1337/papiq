from datetime import UTC, datetime

import pytest

from papiq.core.domain.errors import NotFoundError, ValidationError
from papiq.core.domain.ids import TagId
from papiq.core.domain.master_data import Tag, TagScope
from papiq.core.domain.tag_tree import MAX_LEVELS, SEPARATOR, TagTree

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def tag(name: str, parent: Tag | None = None, scope: TagScope = TagScope.DOCUMENT) -> Tag:
    return Tag.create(
        name=name, now=NOW, scope=scope, parent_id=None if parent is None else parent.id
    )


@pytest.fixture
def insurance() -> dict[str, Tag]:
    """Insurance with Health (with Dental) and Car; Bills; a contact group named Insurance."""
    top = tag("Insurance")
    health = tag("Health", top)
    dental = tag("Dental", health)
    car = tag("Car", top)
    bills = tag("Bills")
    group = tag("Insurance", scope=TagScope.CONTACT)
    return {
        "insurance": top,
        "health": health,
        "dental": dental,
        "car": car,
        "bills": bills,
        "group": group,
    }


def test_ancestors_descendants_and_paths(insurance: dict[str, Tag]) -> None:
    tree = TagTree.of(insurance.values())
    top, health, dental, car = (insurance[k] for k in ("insurance", "health", "dental", "car"))
    assert tree.ancestors(dental.id) == [health, top]
    assert tree.ancestors(top.id) == []
    assert [item.name for item in tree.descendants(top.id)] == ["Car", "Health", "Dental"]
    assert tree.with_descendants([health.id]) == {health.id, dental.id}
    assert tree.with_ancestors([dental.id, car.id]) == {dental.id, health.id, top.id, car.id}
    assert tree.path(dental.id) == ["Insurance", "Health", "Dental"]
    assert tree.label(dental.id) == SEPARATOR.join(["Insurance", "Health", "Dental"])
    assert (tree.depth(dental.id), tree.height(top.id), tree.height(dental.id)) == (3, 3, 1)
    unknown = TagId(Tag.create(name="x", now=NOW).id)
    assert tree.ancestors(unknown) == [] and tree.with_descendants([unknown]) == {unknown}
    with pytest.raises(NotFoundError):
        tree.path(unknown)


def test_a_tag_holds_the_members_with_it_or_a_tag_below_it_once(
    insurance: dict[str, Tag],
) -> None:
    tree = TagTree.of(insurance.values())
    top, health, dental, car = (insurance[k] for k in ("insurance", "health", "dental", "car"))
    members = [{dental.id}, {health.id, dental.id}, {car.id}, set()]
    assert tree.holders(members) == {top.id: 3, health.id: 2, dental.id: 2, car.id: 1}
    assert tree.holders([]) == {}


def test_tags_of_a_scope_in_the_order_of_the_tree(insurance: dict[str, Tag]) -> None:
    tree = TagTree.of(insurance.values())
    names = [item.name for item in tree.of_scope(TagScope.DOCUMENT)]
    assert names == ["Bills", "Insurance", "Car", "Health", "Dental"]
    assert tree.of_scope(TagScope.CONTACT) == [insurance["group"]]
    assert tree.of_scope(TagScope.DOCUMENT_TYPE) == []


def test_a_parent_of_another_scope_or_below_is_refused(insurance: dict[str, Tag]) -> None:
    tree = TagTree.of(insurance.values())
    top, dental = insurance["insurance"], insurance["dental"]
    tree.check_parent(insurance["bills"], top.id)
    tree.check_parent(top, None)
    with pytest.raises(ValidationError, match="another scope"):
        tree.check_parent(insurance["group"], top.id)
    with pytest.raises(ValidationError, match="below it"):
        tree.check_parent(top, dental.id)
    with pytest.raises(ValidationError, match="below it"):
        tree.check_parent(top, top.id)
    with pytest.raises(NotFoundError):
        tree.check_parent(top, Tag.create(name="gone", now=NOW).id)


def test_at_most_five_levels_with_the_subtree(insurance: dict[str, Tag]) -> None:
    levels = [tag("1")]
    for number in range(2, MAX_LEVELS + 1):
        levels.append(tag(str(number), levels[-1]))
    tree = TagTree.of([*insurance.values(), *levels])
    tree.check_parent(tag("new"), levels[-2].id)  # a new tag as the fifth level
    with pytest.raises(ValidationError, match="5 levels"):
        tree.check_parent(tag("new"), levels[-1].id)
    # Insurance carries two levels below it: under the third level it would reach six.
    tree.check_parent(insurance["insurance"], levels[1].id)
    with pytest.raises(ValidationError, match="5 levels"):
        tree.check_parent(insurance["insurance"], levels[2].id)


def test_normalise_keeps_the_child_of_a_parent_set_with_it(insurance: dict[str, Tag]) -> None:
    tree = TagTree.of(insurance.values())
    top, health, dental, bills = (insurance[k] for k in ("insurance", "health", "dental", "bills"))
    assert tree.normalise([top.id, dental.id, bills.id]) == {dental.id, bills.id}
    assert tree.normalise([top.id]) == {top.id}  # a parent alone is fine
    assert tree.normalise([health.id, insurance["car"].id]) == {health.id, insurance["car"].id}
    assert tree.normalise([]) == frozenset()


def test_a_cycle_in_stored_data_does_not_hang() -> None:
    first, second = tag("First"), tag("Second")
    first.parent_id, second.parent_id = second.id, first.id
    tree = TagTree.of([first, second, tag("Top")])
    assert tree.ancestors(first.id) == [second]
    assert tree.height(first.id) == 2
    # No top-level tag leads to them: they come last, as if at the top, and stay visible.
    assert [item.name for item in tree.of_scope(TagScope.DOCUMENT)] == ["Top", "First", "Second"]

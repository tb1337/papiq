from datetime import UTC, datetime

import pytest

from papiq.core.domain.drawers import DEFAULT_DRAWER_NAME, Drawer, ShareLevel
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import UserId, new_id
from papiq.core.domain.users import Role, User

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def test_user_has_a_role() -> None:
    admin = User.create(username=" alice ", role=Role.ADMIN, now=NOW)
    user = User.create(username="bob", role=Role.USER, now=NOW)
    assert admin.username == "alice"
    assert admin.is_admin
    assert not user.is_admin


def test_user_needs_a_name_and_utc_time() -> None:
    with pytest.raises(ValidationError):
        User.create(username=" ", role=Role.USER, now=NOW)
    with pytest.raises(ValidationError):
        User.create(username="bob", role=Role.USER, now=datetime(2026, 1, 1))


def test_default_drawer_is_private() -> None:
    owner = UserId(new_id())
    drawer = Drawer.create_default(owner_id=owner, now=NOW)
    assert drawer.is_default
    assert drawer.name == DEFAULT_DRAWER_NAME
    with pytest.raises(ValidationError, match="default drawer cannot be shared"):
        drawer.share(UserId(new_id()), ShareLevel.READ)


def test_drawer_can_be_shared_and_unshared() -> None:
    owner, other = UserId(new_id()), UserId(new_id())
    drawer = Drawer.create(owner_id=owner, name="Household", now=NOW)
    drawer.share(other, ShareLevel.READ)
    drawer.share(other, ShareLevel.READ_WRITE)
    assert drawer.shares == {other: ShareLevel.READ_WRITE}
    drawer.unshare(other)
    drawer.unshare(other)
    assert drawer.shares == {}


def test_drawer_is_not_shared_with_its_owner() -> None:
    owner = UserId(new_id())
    drawer = Drawer.create(owner_id=owner, name="Household", now=NOW)
    with pytest.raises(ValidationError, match="owner"):
        drawer.share(owner, ShareLevel.READ)


def test_drawer_invariants_hold_on_construction() -> None:
    owner, other = UserId(new_id()), UserId(new_id())
    with pytest.raises(ValidationError):
        Drawer(
            id=Drawer.create(owner_id=owner, name="x", now=NOW).id,
            owner_id=owner,
            name="Default",
            is_default=True,
            shares={other: ShareLevel.READ},
            created_at=NOW,
        )


def test_drawer_rename_requires_a_name() -> None:
    drawer = Drawer.create(owner_id=UserId(new_id()), name="Household", now=NOW)
    drawer.rename(" Taxes ")
    assert drawer.name == "Taxes"
    with pytest.raises(ValidationError):
        drawer.rename("")


def test_share_level_write() -> None:
    assert ShareLevel.READ_WRITE.can_write
    assert not ShareLevel.READ.can_write

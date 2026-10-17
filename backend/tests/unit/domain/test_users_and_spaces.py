from datetime import UTC, datetime

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import UserId, new_id
from papiq.core.domain.spaces import DEFAULT_SPACE_NAME, ShareLevel, Space
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


def test_default_space_is_private() -> None:
    owner = UserId(new_id())
    space = Space.create_default(owner_id=owner, now=NOW)
    assert space.is_default
    assert space.name == DEFAULT_SPACE_NAME
    with pytest.raises(ValidationError, match="default space cannot be shared"):
        space.share(UserId(new_id()), ShareLevel.READ)


def test_space_can_be_shared_and_unshared() -> None:
    owner, other = UserId(new_id()), UserId(new_id())
    space = Space.create(owner_id=owner, name="Household", now=NOW)
    space.share(other, ShareLevel.READ)
    space.share(other, ShareLevel.READ_WRITE)
    assert space.shares == {other: ShareLevel.READ_WRITE}
    space.unshare(other)
    space.unshare(other)
    assert space.shares == {}


def test_space_is_not_shared_with_its_owner() -> None:
    owner = UserId(new_id())
    space = Space.create(owner_id=owner, name="Household", now=NOW)
    with pytest.raises(ValidationError, match="owner"):
        space.share(owner, ShareLevel.READ)


def test_space_invariants_hold_on_construction() -> None:
    owner, other = UserId(new_id()), UserId(new_id())
    with pytest.raises(ValidationError):
        Space(
            id=Space.create(owner_id=owner, name="x", now=NOW).id,
            owner_id=owner,
            name="Default",
            is_default=True,
            shares={other: ShareLevel.READ},
            created_at=NOW,
        )


def test_space_rename_requires_a_name() -> None:
    space = Space.create(owner_id=UserId(new_id()), name="Household", now=NOW)
    space.rename(" Taxes ")
    assert space.name == "Taxes"
    with pytest.raises(ValidationError):
        space.rename("")


def test_share_level_write() -> None:
    assert ShareLevel.READ_WRITE.can_write
    assert not ShareLevel.READ.can_write

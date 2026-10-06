"""Drawers: the unit of filing and of permissions. Rights hang on drawers, not documents."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Self

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DrawerId, UserId, new_id
from papiq.core.domain.validation import require_name, require_utc

DEFAULT_DRAWER_NAME = "Default"


class ShareLevel(StrEnum):
    READ = "read"
    READ_WRITE = "read_write"

    @property
    def can_write(self) -> bool:
        return self is ShareLevel.READ_WRITE


@dataclass(kw_only=True)
class Drawer:
    """A drawer has one owner and may be shared with other users.

    Every user has exactly one default drawer; it is private (never shared) and cannot be deleted.
    Names are unique per owner regardless of case.
    """

    id: DrawerId
    owner_id: UserId
    name: str
    is_default: bool = False
    shares: dict[UserId, ShareLevel] = field(default_factory=dict)
    created_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        self.name = require_name(self.name, "drawer name")
        self.created_at = require_utc(self.created_at, "created_at")
        if self.owner_id in self.shares:
            raise ValidationError("a drawer cannot be shared with its owner")
        if self.is_default and self.shares:
            raise ValidationError("the default drawer cannot be shared")

    @classmethod
    def create(cls, *, owner_id: UserId, name: str, now: datetime) -> Self:
        return cls(id=DrawerId(new_id()), owner_id=owner_id, name=name, created_at=now)

    @classmethod
    def create_default(cls, *, owner_id: UserId, now: datetime) -> Self:
        return cls(
            id=DrawerId(new_id()),
            owner_id=owner_id,
            name=DEFAULT_DRAWER_NAME,
            is_default=True,
            created_at=now,
        )

    def rename(self, name: str) -> None:
        self.name = require_name(name, "drawer name")

    def share(self, user_id: UserId, level: ShareLevel) -> None:
        if self.is_default:
            raise ValidationError("the default drawer cannot be shared")
        if user_id == self.owner_id:
            raise ValidationError("a drawer cannot be shared with its owner")
        self.shares[user_id] = level

    def unshare(self, user_id: UserId) -> None:
        self.shares.pop(user_id, None)

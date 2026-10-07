"""Users and roles. Passwords, TOTP and tokens belong to identity (`identity.py`), not to the
user."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from papiq.core.domain.ids import UserId, new_id
from papiq.core.domain.validation import require_name, require_utc


class Role(StrEnum):
    ADMIN = "admin"  # manages users and master data; no extra rights on documents
    USER = "user"


@dataclass(kw_only=True)
class User:
    id: UserId
    username: str  # unique regardless of case
    role: Role
    created_at: datetime
    active: bool = True  # a deactivated user cannot sign in and reads nothing
    version: int = 1

    def __post_init__(self) -> None:
        self.username = require_name(self.username, "username")
        self.created_at = require_utc(self.created_at, "created_at")

    @classmethod
    def create(cls, *, username: str, role: Role, now: datetime) -> Self:
        return cls(id=UserId(new_id()), username=username, role=role, created_at=now)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_active_admin(self) -> bool:
        return self.active and self.is_admin

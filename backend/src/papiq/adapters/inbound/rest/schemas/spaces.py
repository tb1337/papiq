"""Spaces and their shares."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.schemas.common import NameIn
from papiq.core.domain.permissions import can_manage_space, space_access
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User


class SpaceCreate(NameIn):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "Household"}]},
    )

    owner_id: UUID | None = Field(
        default=None,
        description="Admins only: the user who will own the space. Default: the caller.",
    )


class ShareOut(BaseModel):
    user_id: UUID
    level: ShareLevel


class SpaceOut(BaseModel):
    id: UUID
    name: str = Field(examples=["Household"])
    owner_id: UUID
    is_default: bool
    access: ShareLevel = Field(
        description="The caller's access: owner and admins (read_write), or the share."
    )
    shares: list[ShareOut] | None = Field(description="Only for the owner and admins.")
    created_at: datetime

    @classmethod
    def of(cls, space: Space, viewer: User) -> "SpaceOut":
        """As `viewer` sees it, who owns it, has a share or is an admin."""
        access = space_access(viewer, space)
        assert access is not None  # the service returns visible spaces only
        manager = can_manage_space(viewer, space)
        shares = sorted(space.shares.items())
        return cls(
            id=space.id,
            name=space.name,
            owner_id=space.owner_id,
            is_default=space.is_default,
            access=access,
            shares=[ShareOut(user_id=u, level=level) for u, level in shares] if manager else None,
            created_at=space.created_at,
        )


class ShareIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"level": "read"}]},
    )

    level: ShareLevel

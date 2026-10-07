"""Who calls the API.

Authentication comes with M4. Until then every request is unauthenticated: `current_user`
always answers 401, and nothing in the running service can name a user. Tests replace the
dependency (`app.dependency_overrides[current_user]`); M4 replaces its implementation.
"""

from typing import Annotated

from fastapi import Depends

from papiq.adapters.inbound.rest.problems import AuthenticationRequiredError
from papiq.core.domain.ids import UserId


async def current_user() -> UserId:
    """The authenticated user."""
    raise AuthenticationRequiredError("authentication is required (available from M4 on)")


CurrentUser = Annotated[UserId, Depends(current_user)]

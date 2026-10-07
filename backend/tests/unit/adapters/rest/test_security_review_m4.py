"""Failing tests from the M4 security review (`.idea/reviews/M4-security.md`), HTTP level.

Each test encodes the behaviour the review recommends and fails on the reviewed branch on
purpose; the finding it belongs to is named in its docstring.
"""

from papiq.adapters.inbound.rest import PREFIX
from tests.unit.adapters.rest.conftest import Api

NEW_PASSWORD = "a brand new passphrase"


# --- M4-04: JSON bodies have no size limit -----------------------------------------------------


async def test_json_bodies_are_bounded(api: Api) -> None:
    """M4-04: only multipart uploads have a size limit (`PAPIQ_UPLOAD_MAX_SIZE`). A JSON body of
    any size is read into memory before validation, also at the public sign-in."""
    huge = '{"username": "' + "a" * (16 * 1024 * 1024) + '", "password": "x"}'
    response = await api.client.post(
        f"{PREFIX}/auth/login", content=huge, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413, response.status_code

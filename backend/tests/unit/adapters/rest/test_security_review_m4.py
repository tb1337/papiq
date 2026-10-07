"""Failing tests from the M4 security review (`.idea/reviews/M4-security.md`), HTTP level.

Each test encodes the behaviour the review recommends and fails on the reviewed branch on
purpose; the finding it belongs to is named in its docstring.
"""

from papiq.adapters.inbound.rest import PREFIX
from tests.api import auth
from tests.unit.adapters.rest.conftest import Api

NEW_PASSWORD = "a brand new passphrase"


# --- M4-03: an admin's API token takes over the admin's account ---------------------------------


async def test_an_admin_token_cannot_reset_the_admins_own_password(api: Api) -> None:
    """M4-03: managing the own sign-in needs a session (`/auth/*`), but the admin endpoints
    accept a `read_write` token and do not exclude the caller's own account. A leaked admin
    token resets the admin's password, turns TOTP off and signs in with a session: full,
    persistent control, which the token alone was not meant to give."""
    admin = await api.admin("root")
    headers = auth(admin)  # a `read_write` API token
    reset = await api.client.post(
        f"{PREFIX}/users/{admin.id}/password", json={"password": NEW_PASSWORD}, headers=headers
    )
    assert reset.status_code == 403, reset.text
    totp_off = await api.client.delete(f"{PREFIX}/users/{admin.id}/totp", headers=headers)
    assert totp_off.status_code == 403, totp_off.text


async def test_an_admin_token_does_not_lead_to_a_session(api: Api) -> None:
    """M4-03, the chain: token -> password reset -> session -> new tokens, TOTP, everything."""
    admin = await api.admin("root")
    await api.client.post(
        f"{PREFIX}/users/{admin.id}/password", json={"password": NEW_PASSWORD}, headers=auth(admin)
    )
    response = await api.client.post(
        f"{PREFIX}/auth/login", json={"username": "root", "password": NEW_PASSWORD}
    )
    assert response.status_code == 401, "the token's holder signed in with a session"


# --- M4-04: JSON bodies have no size limit -----------------------------------------------------


async def test_json_bodies_are_bounded(api: Api) -> None:
    """M4-04: only multipart uploads have a size limit (`PAPIQ_UPLOAD_MAX_SIZE`). A JSON body of
    any size is read into memory before validation, also at the public sign-in."""
    huge = '{"username": "' + "a" * (16 * 1024 * 1024) + '", "password": "x"}'
    response = await api.client.post(
        f"{PREFIX}/auth/login", content=huge, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413, response.status_code

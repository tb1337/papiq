"""Failing tests from the M4 security review (`.idea/reviews/M4-security.md`), service level.

Each test encodes the behaviour the review recommends and fails on the reviewed branch on
purpose; the finding it belongs to is named in its docstring.
"""

import logging

import pytest

from papiq.core.domain.errors import AuthenticationError
from tests.unit.services.conftest import World

SOURCE = "203.0.113.1"
DEV_KEY = "ZGV2LWtleS1kZXYta2V5LWRldi1rZXktZGV2LWtleS0="


# --- M4-07: the typed username is logged -------------------------------------------------------


async def test_failed_sign_ins_do_not_log_the_typed_username(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """M4-07: `sign-in failed` logs the account key, i.e. the username as typed. A password
    typed into the username field (a common slip) ends up in the log."""
    typed_into_the_wrong_field = "my secret passphrase 42"
    with (
        caplog.at_level(logging.INFO, logger="papiq.core.services.auth"),
        pytest.raises(AuthenticationError),
    ):
        await world.auth.login(typed_into_the_wrong_field, "x", source=SOURCE)
    for record in caplog.records:
        logged = record.getMessage() + repr(record.__dict__.get("keys"))
        assert typed_into_the_wrong_field.casefold() not in logged.casefold(), logged

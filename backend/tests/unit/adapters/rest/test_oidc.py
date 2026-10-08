"""OpenID Connect over HTTP, with the fake provider."""

from collections.abc import AsyncIterator
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.memory import FakeOidcProvider, ManualClock
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.errors import AuthenticationError
from papiq.core.domain.identity import ExternalIdentity
from papiq.core.domain.ids import UserId
from tests import builders
from tests.api import auth
from tests.builders import NOW
from tests.unit.adapters.rest.conftest import Api, make_app

OIDC = f"{PREFIX}/auth/oidc"


@pytest.fixture
async def oidc_api() -> AsyncIterator[tuple[Api, FakeOidcProvider]]:
    clock = ManualClock(builders.NOW)
    provider = FakeOidcProvider()
    container = replace(build_memory_container(clock), oidc=provider)
    services = build_services(container)
    app = make_app(container, services)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://papiq") as client:
        yield Api(container, services, app, client, clock), provider


def state(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


async def link(api: Api, subject: str, user_id: UserId) -> None:
    async with api.container.unit_of_work() as uow:
        await uow.external_identities.add(
            ExternalIdentity.link(
                issuer="https://idp.test", subject=subject, user_id=user_id, now=NOW
            )
        )
        await uow.commit()


async def test_sign_in_through_the_provider(oidc_api: tuple[Api, FakeOidcProvider]) -> None:
    api, provider = oidc_api
    user = await api.user("alice")
    await link(api, "sub-alice", user.id)
    info = (await api.client.get(OIDC)).json()
    assert info == {"enabled": True, "display_name": "Single sign-on"}

    start = await api.client.get(f"{OIDC}/login", params={"next": "/inbox"})
    assert start.status_code == 302
    url = start.headers["location"]
    assert url.startswith("https://idp.test/authorize?")
    flow_cookie = start.headers["set-cookie"]
    assert flow_cookie.startswith("__Host-papiq_oidc=") and "httponly" in flow_cookie.lower()

    code = provider.consent(url, "sub-alice")
    back = await api.client.get(f"{OIDC}/callback", params={"code": code, "state": state(url)})
    assert back.status_code == 303, back.text
    assert back.headers["location"] == "/inbox"
    assert "__Host-papiq_session=" in str(back.headers.get_list("set-cookie"))
    me = (await api.client.get(f"{PREFIX}/auth/me")).json()
    assert me["user"]["username"] == "alice"


async def test_the_callback_needs_the_browser_that_started(
    oidc_api: tuple[Api, FakeOidcProvider],
) -> None:
    api, provider = oidc_api
    user = await api.user("alice")
    await link(api, "sub-alice", user.id)
    url = (await api.client.get(f"{OIDC}/login")).headers["location"]
    code = provider.consent(url, "sub-alice")
    async with api.new_client() as other_browser:
        stolen = await other_browser.get(
            f"{OIDC}/callback", params={"code": code, "state": state(url)}
        )
    # A failure sends the browser back to the web UI with a code, not to a problem document.
    assert stolen.status_code == 303
    assert stolen.headers["location"] == "/ui/login?error=failed"
    wrong_state = await api.client.get(f"{OIDC}/callback", params={"code": code, "state": "x"})
    assert wrong_state.headers["location"] == "/ui/login?error=failed"
    refused = await api.client.get(f"{OIDC}/callback", params={"error": "access_denied"})
    assert refused.status_code == 303
    assert refused.headers["location"] == "/ui/login?error=denied"
    assert (await api.client.get(f"{OIDC}/callback")).headers["location"] == (
        "/ui/login?error=denied"
    )


async def test_unlinked_accounts_are_refused(oidc_api: tuple[Api, FakeOidcProvider]) -> None:
    api, provider = oidc_api
    await api.user("alice")
    url = (await api.client.get(f"{OIDC}/login")).headers["location"]
    code = provider.consent(url, "sub-unknown", username="alice")
    response = await api.client.get(f"{OIDC}/callback", params={"code": code, "state": state(url)})
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=failed"
    assert (await api.client.get(f"{PREFIX}/auth/me")).status_code == 401


async def test_redirects_stay_on_this_site(oidc_api: tuple[Api, FakeOidcProvider]) -> None:
    api, provider = oidc_api
    user = await api.user("alice")
    await link(api, "sub-alice", user.id)
    for target in ("https://evil.example/", "//evil.example", "/\\evil.example"):
        url = (await api.client.get(f"{OIDC}/login", params={"next": target})).headers["location"]
        code = provider.consent(url, "sub-alice")
        back = await api.client.get(f"{OIDC}/callback", params={"code": code, "state": state(url)})
        assert back.headers["location"] == "/", target


async def test_linking_through_the_api(oidc_api: tuple[Api, FakeOidcProvider]) -> None:
    api, provider = oidc_api
    user = await api.user("alice")
    async with api.sign_in(user) as session:
        started = await session.client.post(f"{OIDC}/link", headers=session.headers)
        assert started.status_code == 200
        url = started.json()["authorization_url"]
        code = provider.consent(url, "sub-alice")
        back = await session.client.get(
            f"{OIDC}/callback", params={"code": code, "state": state(url)}
        )
        assert back.status_code == 303
        me = (await session.client.get(f"{PREFIX}/auth/me")).json()
        assert [link["subject"] for link in me["linked_accounts"]] == ["sub-alice"]
        removed = await session.client.delete(f"{OIDC}/link", headers=session.headers)
        assert removed.json() == {"removed": 1}


async def test_without_a_provider(api: Api) -> None:
    assert (await api.client.get(OIDC)).json() == {"enabled": False, "display_name": None}
    assert (await api.client.get(f"{OIDC}/login")).status_code == 404
    user = await api.user()
    async with api.sign_in(user) as session:
        response = await session.client.post(f"{OIDC}/link", headers=session.headers)
        assert response.status_code == 404
    assert (await api.client.get(f"{PREFIX}/auth/me", headers=auth(user))).status_code == 200


async def test_signing_in_ends_the_session_the_browser_had(
    oidc_api: tuple[Api, FakeOidcProvider],
) -> None:
    """M4-06: a session the browser can no longer see must not stay valid."""
    api, provider = oidc_api
    alice, bob = await api.user("alice"), await api.user("bob")
    await link(api, "sub-bob", bob.id)
    async with api.sign_in(alice) as session:
        old_cookie = session.client.cookies.get("__Host-papiq_session")
        url = (await session.client.get(f"{OIDC}/login")).headers["location"]
        code = provider.consent(url, "sub-bob")
        back = await session.client.get(
            f"{OIDC}/callback", params={"code": code, "state": state(url)}
        )
        assert back.status_code == 303
        me = (await session.client.get(f"{PREFIX}/auth/me")).json()
        assert me["user"]["username"] == "bob"
    assert old_cookie is not None
    with pytest.raises(AuthenticationError):
        await api.services.auth.authenticate_session(old_cookie)


async def test_a_failed_link_returns_to_the_settings(
    oidc_api: tuple[Api, FakeOidcProvider],
) -> None:
    api, provider = oidc_api
    alice, bob = await api.user("alice"), await api.user("bob")
    await link(api, "sub-bob", bob.id)
    async with api.sign_in(alice) as session:
        started = await session.client.post(f"{OIDC}/link", headers=session.headers)
        url = started.json()["authorization_url"]
        code = provider.consent(url, "sub-bob")
        back = await session.client.get(
            f"{OIDC}/callback", params={"code": code, "state": state(url)}
        )
    assert back.status_code == 303
    assert back.headers["location"] == "/ui/settings?error=conflict"

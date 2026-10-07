"""Authentication over HTTP: no endpoint without it, sessions and their cookie, CSRF, API tokens
and their scope, the own account."""

import re
from datetime import timedelta
from uuid import UUID

import httpx2
import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.rest.auth import CSRF_HEADER
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.identity import TokenScope
from tests.api import auth, bearer
from tests.builders import PASSWORD
from tests.unit.adapters.rest.conftest import Api, make_app

PUBLIC = {
    ("POST", f"{PREFIX}/auth/login"),
    ("GET", f"{PREFIX}/auth/oidc"),
    ("GET", f"{PREFIX}/auth/oidc/login"),
    ("GET", f"{PREFIX}/auth/oidc/callback"),
    ("GET", f"{PREFIX}/health"),
}
NEW_PASSWORD = "another long passphrase"
PROBLEM = "application/problem+json"


def templates(api: Api) -> list[tuple[str, str]]:
    """Every registered API route: method and path template."""
    return [
        (method, context.path_format or "")
        for context in iter_route_contexts(api.app.routes)
        if isinstance(context.original_route, APIRoute)
        for method in sorted(context.methods or ())
    ]


def test_the_public_routes_are_exactly_these(api: Api) -> None:
    registered = set(templates(api))
    assert registered >= PUBLIC
    openapi = api.app.openapi()
    public = {
        (method.upper(), path)
        for path, item in openapi["paths"].items()
        for method, operation in item.items()
        if operation["security"] == []
    }
    assert public == PUBLIC


async def test_every_other_route_needs_authentication(api: Api) -> None:
    """New routes are protected by default: this fails for a route that is not."""
    checked = 0
    for method, template in templates(api):
        if (method, template) in PUBLIC:
            continue
        path = re.sub(r"\{[^}]+\}", str(UUID(int=1)), template)
        for headers in ({}, {"Authorization": "Bearer papiq_wrong"}, {"Authorization": "Basic x"}):
            response = await api.client.request(method, path, headers=headers, json={})
            assert response.status_code == 401, (method, path, headers, response.text)
            assert response.headers["content-type"] == PROBLEM
            assert response.headers["www-authenticate"] == "Bearer"
        api.client.cookies.set("__Host-papiq_session", "forged")
        response = await api.client.request(method, path, json={})
        api.client.cookies.clear()
        assert response.status_code == 401, (method, path)
        checked += 1
    assert checked >= 50


# --- sessions -----------------------------------------------------------------------------------


async def test_login_sets_a_secure_cookie(api: Api) -> None:
    user = await api.user("alice")
    response = await api.client.post(
        f"{PREFIX}/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == str(user.id) and body["method"] == "password"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-papiq_session=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert attribute.lower() in cookie.lower(), attribute
    assert "Domain" not in cookie
    token = cookie.split(";")[0].split("=", 1)[1]
    assert token not in response.text
    me = await api.client.get(f"{PREFIX}/auth/me")
    assert me.json()["authenticated_with"] == "session"
    assert me.json()["csrf_token"] == body["csrf_token"]


async def test_insecure_cookies_only_when_configured() -> None:
    container = build_memory_container()
    services = build_services(container)
    await services.users.bootstrap_admin("alice", PASSWORD)
    app = make_app(container, services, cookie_secure=False)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://papiq") as client:
        response = await client.post(
            f"{PREFIX}/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("papiq_session=") and "secure" not in cookie.lower()
        assert (await client.get(f"{PREFIX}/auth/me")).status_code == 200


async def test_failed_logins_look_alike(api: Api) -> None:
    await api.user("alice")
    answers = [
        await api.client.post(f"{PREFIX}/auth/login", json={"username": name, "password": pw})
        for name, pw in [("alice", "wrong password!"), ("nobody", PASSWORD)]
    ]
    assert [a.status_code for a in answers] == [401, 401]
    assert answers[0].json() == answers[1].json()
    assert "set-cookie" not in answers[0].headers


async def test_login_only_as_json(api: Api) -> None:
    await api.user("alice")
    response = await api.client.post(
        f"{PREFIX}/auth/login", data={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 415
    response = await api.client.post(
        f"{PREFIX}/auth/login",
        content=f'{{"username": "alice", "password": "{PASSWORD}"}}',
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 415


async def test_too_many_failures_answer_429(api: Api) -> None:
    await api.user("alice")
    for _ in range(6):
        await api.client.post(
            f"{PREFIX}/auth/login", json={"username": "alice", "password": "wrong password!"}
        )
    response = await api.client.post(
        f"{PREFIX}/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


async def test_changes_with_a_session_need_the_csrf_token(api: Api) -> None:
    user = await api.user("alice")
    async with api.sign_in(user) as session:
        client = session.client
        url = f"{PREFIX}/drawers"
        assert (await client.get(url)).status_code == 200  # reading needs none
        missing = await client.post(url, json={"name": "A"})
        assert missing.status_code == 403
        assert CSRF_HEADER in missing.json()["detail"]
        wrong = await client.post(url, json={"name": "A"}, headers={CSRF_HEADER: "x" * 64})
        assert wrong.status_code == 403
        right = await client.post(url, json={"name": "A"}, headers=session.headers)
        assert right.status_code == 201
        # Another session's CSRF token does not fit.
        async with api.sign_in(user) as other:
            pass
        crossed = await client.post(url, json={"name": "B"}, headers=other.headers)
        assert crossed.status_code == 403


async def test_logout_ends_the_session(api: Api) -> None:
    async with api.sign_in(await api.user()) as session:
        client = session.client
        response = await client.post(f"{PREFIX}/auth/logout", headers=session.headers)
        assert response.status_code == 204
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert (await client.get(f"{PREFIX}/auth/me")).status_code == 401


async def test_sessions_expire(api: Api) -> None:
    async with api.sign_in(await api.user()) as session:
        client = session.client
        api.clock.advance(timedelta(days=1))
        assert (await client.get(f"{PREFIX}/auth/me")).status_code == 401


async def test_second_factor_over_http(api: Api) -> None:
    user = await api.user("alice")
    async with api.sign_in(user) as session:
        client = session.client
        setup = await client.post(f"{PREFIX}/auth/totp", headers=session.headers)
        assert setup.status_code == 201
        secret = setup.json()["secret"]
        code = api.container.totp.code(secret, api.clock.now())
        confirmed = await client.post(
            f"{PREFIX}/auth/totp/confirm", json={"code": code}, headers=session.headers
        )
        assert len(confirmed.json()["recovery_codes"]) == 10
    login = {"username": "alice", "password": PASSWORD}
    response = await api.client.post(f"{PREFIX}/auth/login", json=login)
    assert response.status_code == 401
    assert response.json()["second_factor_required"] is True
    api.clock.advance(timedelta(seconds=30))
    code = api.container.totp.code(secret, api.clock.now())
    response = await api.client.post(f"{PREFIX}/auth/login", json={**login, "code": code})
    assert response.status_code == 200
    recovery = confirmed.json()["recovery_codes"][0]
    response = await api.client.post(
        f"{PREFIX}/auth/login", json={**login, "recovery_code": recovery}
    )
    assert response.status_code == 200
    me = (await api.client.get(f"{PREFIX}/auth/me")).json()
    assert me["totp_enabled"] is True


async def test_password_change_over_http(api: Api) -> None:
    user = await api.user("alice")
    async with api.sign_in(user) as session, api.sign_in(user) as other:
        client, other_client = session.client, other.client
        response = await client.post(
            f"{PREFIX}/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=session.headers,
        )
        assert response.status_code == 200
        assert "__Host-papiq_session=" in response.headers["set-cookie"]
        new_csrf = response.json()["csrf_token"]
        assert new_csrf != session.csrf_token
        assert (await client.get(f"{PREFIX}/auth/me")).status_code == 200  # renewed
        assert (await other_client.get(f"{PREFIX}/auth/me")).status_code == 401
    assert (await api.client.get(f"{PREFIX}/auth/me", headers=auth(user))).status_code == 200


# --- API tokens ---------------------------------------------------------------------------------


async def test_tokens_over_http(api: Api) -> None:
    user = await api.user()
    async with api.sign_in(user) as session:
        client = session.client
        created = await client.post(
            f"{PREFIX}/auth/tokens",
            json={"name": "scanner", "scope": "read"},
            headers=session.headers,
        )
        assert created.status_code == 201
        token = created.json()["token"]
        assert token.startswith("papiq_")
        listed = (await client.get(f"{PREFIX}/auth/tokens")).json()
        assert [t["name"] for t in listed] == ["tests", "scanner"]
        assert token not in str(listed)

        read = bearer(token)
        me = (await api.client.get(f"{PREFIX}/auth/me", headers=read)).json()
        assert me["authenticated_with"] == "token" and me["token_scope"] == "read"
        assert me["csrf_token"] is None
        assert (await api.client.get(f"{PREFIX}/drawers", headers=read)).status_code == 200
        denied = await api.client.post(f"{PREFIX}/drawers", json={"name": "A"}, headers=read)
        assert denied.status_code == 403

        id = created.json()["id"]
        revoked = await client.delete(f"{PREFIX}/auth/tokens/{id}", headers=session.headers)
        assert revoked.status_code == 204
        assert (await api.client.get(f"{PREFIX}/auth/me", headers=read)).status_code == 401


async def test_a_bearer_header_wins_over_a_cookie(api: Api) -> None:
    alice, bob = await api.user("alice"), await api.user("bob")
    async with api.sign_in(alice) as session:
        client = session.client
        me = await client.get(f"{PREFIX}/auth/me", headers=auth(bob))
        assert me.json()["user"]["username"] == "bob"
        broken = await client.get(f"{PREFIX}/auth/me", headers=bearer("papiq_wrong"))
        assert broken.status_code == 401


async def test_expired_tokens_are_refused(api: Api) -> None:
    user = await api.user()
    _, token = await api.services.auth.create_api_token(
        user.id, "short", TokenScope.READ, expires_at=api.clock.now() + timedelta(hours=1)
    )
    assert (await api.client.get(f"{PREFIX}/auth/me", headers=bearer(token))).status_code == 200
    api.clock.advance(timedelta(hours=1))
    assert (await api.client.get(f"{PREFIX}/auth/me", headers=bearer(token))).status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/auth/logout", None),
        ("POST", "/auth/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
        ("DELETE", "/auth/sessions", None),
        ("POST", "/auth/totp", None),
        ("POST", "/auth/totp/confirm", {"code": "123456"}),
        ("POST", "/auth/totp/disable", {"code": "123456"}),
        ("POST", "/auth/totp/recovery-codes", {"code": "123456"}),
        ("GET", "/auth/tokens", None),
        ("POST", "/auth/tokens", {"name": "x", "scope": "read_write"}),
        ("DELETE", f"/auth/tokens/{UUID(int=1)}", None),
        ("POST", "/auth/oidc/link", None),
        ("DELETE", "/auth/oidc/link", None),
    ],
)
async def test_tokens_cannot_manage_the_sign_in(
    api: Api, method: str, path: str, body: object
) -> None:
    user = await api.user()
    response = await api.client.request(method, PREFIX + path, json=body, headers=auth(user))
    assert response.status_code == 403, response.text
    assert "session" in response.json()["detail"]


async def test_deactivated_accounts_are_refused(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    async with api.sign_in(user) as session:
        response = await api.client.patch(
            f"{PREFIX}/users/{user.id}", json={"active": False}, headers=auth(admin)
        )
        assert response.status_code == 200
        assert (await session.client.get(f"{PREFIX}/auth/me")).status_code == 401
    assert (await api.client.get(f"{PREFIX}/auth/me", headers=auth(user))).status_code == 401

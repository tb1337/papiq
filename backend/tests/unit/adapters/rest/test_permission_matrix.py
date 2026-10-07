"""The rights matrix over HTTP: every actor against every endpoint that touches a document,
a drawer or master data. Each case runs on a fresh scene.

Actors: the owner; a share `read`; a share `read_write`; a stranger; an admin (no share); the
owner's `read` token; an expired token; a revoked token; a deactivated user who had a share.

Documents in the shared drawer: green (others may see it), yellow and in processing (owner
only). Hidden documents and drawers are "not found" (404), exactly as missing ones.
"""

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.identity import TokenScope
from papiq.core.domain.pipeline import Step
from papiq.core.domain.users import User
from papiq.core.services.objects import archive_key, preview_key
from tests.api import auth, bearer
from tests.builders import UNCERTAIN
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api

ACTORS = [
    "owner",
    "reader",
    "writer",
    "stranger",
    "admin",
    "read_token",
    "expired_token",
    "revoked_token",
    "deactivated",
]


@dataclass
class Scene:
    api: Api
    headers: dict[str, dict[str, str]]
    owner: User
    shared: Drawer
    own_drawer: Drawer  # another drawer of the owner, target for moving
    green: str
    yellow: str
    processing: str
    contact: str

    def document(self, which: str) -> str:
        return str(getattr(self, which))


async def build_scene(api: Api) -> Scene:
    owner, reader, writer = await api.user("owner"), await api.user("reader"), await api.user()
    stranger, admin = await api.user("stranger"), await api.admin("admin")
    gone = await api.user("deactivated")
    drawers = api.services.drawers
    shared = await drawers.create(owner.id, "Shared")
    for user, level in [(reader, ShareLevel.READ), (writer, ShareLevel.READ_WRITE)]:
        await drawers.share(owner.id, shared.id, user.id, level)
    await drawers.share(owner.id, shared.id, gone.id, ShareLevel.READ_WRITE)
    own_drawer = await drawers.create(owner.id, "Private")

    async def upload(name: str) -> str:
        files = {"file": (name, unique_pdf(name), "x/y")}
        response = await api.client.post(
            f"{PREFIX}/documents",
            files=files,
            data={"drawer_id": str(shared.id)},
            headers=auth(owner),
        )
        assert response.status_code == 202, response.text
        await api.drain()
        return str(response.json()["id"])

    green = await upload("green.pdf")
    yellow = await upload("yellow.pdf")
    await api.services.pipeline.reprocess_from(owner.id, UUID(yellow), Step.CLASSIFY)  # type: ignore[arg-type]
    pipeline = api.services.pipeline
    pipeline._executors[Step.CLASSIFY] = _Returns()  # yellow: the classification is uncertain
    await api.drain()
    pipeline._executors.pop(Step.CLASSIFY)
    processing = await upload_without_processing(api, owner, shared)
    for id in (green, yellow):
        await api.container.object_store.put(archive_key(UUID(id)), b"%PDF", content_type="x")  # type: ignore[arg-type]
        await api.container.object_store.put(preview_key(UUID(id)), b"RIFF", content_type="x")  # type: ignore[arg-type]
    contact = await api.services.master_data.create_contact(admin.id, "ACME")

    _, read_token = await api.services.auth.create_api_token(owner.id, "r", TokenScope.READ)
    _, expired = await api.services.auth.create_api_token(
        owner.id, "e", TokenScope.READ_WRITE, expires_at=api.clock.now() + timedelta(seconds=1)
    )
    revoked_token, revoked = await api.services.auth.create_api_token(
        owner.id, "x", TokenScope.READ_WRITE
    )
    await api.services.auth.revoke_api_token(owner.id, revoked_token.id)
    api.clock.advance(timedelta(seconds=1))
    await api.services.users.set_active(admin.id, gone.id, False)
    headers = {
        "owner": auth(owner),
        "reader": auth(reader),
        "writer": auth(writer),
        "stranger": auth(stranger),
        "admin": auth(admin),
        "read_token": bearer(read_token),
        "expired_token": bearer(expired),
        "revoked_token": bearer(revoked),
        "deactivated": auth(gone),
    }
    return Scene(
        api, headers, owner, shared, own_drawer, green, yellow, processing, str(contact.id)
    )


def unique_pdf(name: str) -> bytes:
    """The sample scan with a comment, so that every upload is a file of its own."""
    return (SAMPLES / "scan.pdf").read_bytes().rstrip() + f"\n%{name}\n%%EOF\n".encode()


class _Returns:
    async def run(self, document: object) -> Any:
        return UNCERTAIN


async def upload_without_processing(api: Api, owner: User, drawer: Drawer) -> str:
    files = {"file": ("new.pdf", unique_pdf("new"), "x/y")}
    response = await api.client.post(
        f"{PREFIX}/documents", files=files, data={"drawer_id": str(drawer.id)}, headers=auth(owner)
    )
    return str(response.json()["id"])


type Call = Callable[[Scene, httpx2.AsyncClient, dict[str, str], str], Awaitable[httpx2.Response]]


def doc(method: str, suffix: str = "", body: Any = None) -> Call:
    async def call(
        scene: Scene, client: httpx2.AsyncClient, headers: dict[str, str], which: str
    ) -> httpx2.Response:
        url = f"{PREFIX}/documents/{scene.document(which)}{suffix}"
        payload = body(scene) if callable(body) else body
        return await client.request(method, url, json=payload, headers=headers)

    return call


DENIED = {"expired_token": 401, "revoked_token": 401, "deactivated": 401}
HIDDEN = {"stranger": 404, "admin": 404}

# For the green document in the shared drawer.
GREEN: dict[str, tuple[Call, dict[str, int]]] = {
    "read": (
        doc("GET"),
        {"owner": 200, "reader": 200, "writer": 200, "read_token": 200, **HIDDEN, **DENIED},
    ),
    "original": (
        doc("GET", "/original"),
        {"owner": 200, "reader": 200, "writer": 200, "read_token": 200, **HIDDEN, **DENIED},
    ),
    "archive": (
        doc("GET", "/archive"),
        {"owner": 200, "reader": 200, "writer": 200, "read_token": 200, **HIDDEN, **DENIED},
    ),
    "preview": (
        doc("GET", "/preview"),
        {"owner": 200, "reader": 200, "writer": 200, "read_token": 200, **HIDDEN, **DENIED},
    ),
    "change": (
        doc("PATCH", body={"title": "Changed"}),
        {"owner": 200, "reader": 403, "writer": 200, "read_token": 403, **HIDDEN, **DENIED},
    ),
    "move": (
        doc("POST", "/move", lambda scene: {"drawer_id": str(scene.own_drawer.id)}),
        {
            "owner": 204,
            "reader": 403,
            "writer": 403,
            "stranger": 404,
            "admin": 204,  # admins move any document, without read access
            "read_token": 403,
            **DENIED,
        },
    ),
    "delete": (
        doc("DELETE"),
        {"owner": 204, "reader": 403, "writer": 403, "read_token": 403, **HIDDEN, **DENIED},
    ),
    "log": (
        doc("GET", "/log"),
        {"owner": 200, "reader": 403, "writer": 403, "read_token": 200, **HIDDEN, **DENIED},
    ),
    "retry": (
        doc("POST", "/retry"),
        {"owner": 409, "reader": 403, "writer": 403, "read_token": 403, **HIDDEN, **DENIED},
    ),
    "reprocess": (
        doc("POST", "/reprocess", {"from_step": "parse"}),
        {"owner": 202, "reader": 403, "writer": 403, "read_token": 403, **HIDDEN, **DENIED},
    ),
}

CASES = [
    pytest.param(name, actor, expected.get(actor), id=f"{name}-{actor}")
    for name, (_, expected) in GREEN.items()
    for actor in ACTORS
]


@pytest.mark.parametrize(("operation", "actor", "expected"), CASES)
async def test_green_document(api: Api, operation: str, actor: str, expected: int) -> None:
    scene = await build_scene(api)
    call, _ = GREEN[operation]
    response = await call(scene, api.client, scene.headers[actor], "green")
    assert response.status_code == expected, response.text
    if expected == 404:
        assert response.json()["detail"] == f"document {scene.green} not found"


HIDDEN_FROM_SHARES = ["read", "original", "archive", "preview", "change", "delete", "log"]


@pytest.mark.parametrize("which", ["yellow", "processing"])
@pytest.mark.parametrize("operation", HIDDEN_FROM_SHARES)
async def test_yellow_and_processing_documents_are_the_owners_only(
    api: Api, which: str, operation: str
) -> None:
    scene = await build_scene(api)
    call, _ = GREEN[operation]
    for actor in ("reader", "writer", "stranger", "admin"):
        response = await call(scene, api.client, scene.headers[actor], which)
        assert response.status_code == 404, (actor, response.text)
        assert response.json()["detail"] == f"document {scene.document(which)} not found"
    owner = await call(scene, api.client, scene.headers["owner"], which)
    if which == "processing" and operation in {"archive", "preview"}:
        assert owner.status_code == 404  # not made yet
    else:
        assert owner.status_code in {200, 204}, owner.text


async def test_hidden_and_missing_documents_look_alike(api: Api) -> None:
    scene = await build_scene(api)
    missing = "01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b"
    for which, id in (("hidden", scene.yellow), ("missing", missing)):
        response = await api.client.get(f"{PREFIX}/documents/{id}", headers=scene.headers["reader"])
        assert response.status_code == 404, which
        assert response.json() == {
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": f"document {id} not found",
        }


# --- lists, filters, pages ----------------------------------------------------------------------


async def listed(scene: Scene, actor: str, **params: Any) -> set[str]:
    found: set[str] = set()
    cursor = None
    while True:
        query = {**params, "limit": 1, **({"cursor": cursor} if cursor else {})}
        response = await scene.api.client.get(
            f"{PREFIX}/documents", params=query, headers=scene.headers[actor]
        )
        assert response.status_code == 200, response.text
        page = response.json()
        found |= {item["id"] for item in page["items"]}
        cursor = page["next_cursor"]
        if cursor is None:
            return found


async def test_lists_show_only_readable_documents(api: Api) -> None:
    scene = await build_scene(api)
    everything = {scene.green, scene.yellow, scene.processing}
    assert await listed(scene, "owner") == everything
    assert await listed(scene, "read_token") == everything
    for actor in ("reader", "writer"):
        assert await listed(scene, actor) == {scene.green}
        assert await listed(scene, actor, lane="yellow") == set()
        assert await listed(scene, actor, lane="processing") == set()
        assert await listed(scene, actor, drawer_id=str(scene.shared.id)) == {scene.green}
    for actor in ("stranger", "admin"):
        assert await listed(scene, actor) == set()
        assert await listed(scene, actor, drawer_id=str(scene.shared.id)) == set()
        assert await listed(scene, actor, contact_id=scene.contact) == set()
    assert await listed(scene, "owner", lane=["yellow", "processing"]) == {
        scene.yellow,
        scene.processing,
    }
    for actor in ("expired_token", "revoked_token", "deactivated"):
        response = await api.client.get(f"{PREFIX}/documents", headers=scene.headers[actor])
        assert response.status_code == 401


async def test_a_cursor_does_not_reveal_hidden_documents(api: Api) -> None:
    """Paging after a hidden document's id works like after any id: only readable ones."""
    scene = await build_scene(api)
    cursor = base64.urlsafe_b64encode(UUID(scene.yellow).bytes).decode().rstrip("=")
    response = await api.client.get(
        f"{PREFIX}/documents", params={"cursor": cursor}, headers=scene.headers["reader"]
    )
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} <= {scene.green}


# --- event streams ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "which", "expected"),
    [
        ("reader", "yellow", 404),
        ("reader", "processing", 404),
        ("stranger", "green", 404),
        ("admin", "green", 404),
        ("expired_token", "green", 401),
        ("deactivated", "green", 401),
    ],
)
async def test_event_streams_of_one_document(
    api: Api, actor: str, which: str, expected: int
) -> None:
    scene = await build_scene(api)
    response = await api.client.get(
        f"{PREFIX}/events",
        params={"document_id": scene.document(which)},
        headers=scene.headers[actor],
    )
    assert response.status_code == expected


# --- drawers ------------------------------------------------------------------------------------


async def test_drawers(api: Api) -> None:
    scene = await build_scene(api)
    url = f"{PREFIX}/drawers/{scene.shared.id}"
    owner_view = await api.client.get(url, headers=scene.headers["owner"])
    assert owner_view.status_code == 200
    assert {s["level"] for s in owner_view.json()["shares"]} == {"read", "read_write"}
    reader_view = (await api.client.get(url, headers=scene.headers["reader"])).json()
    assert reader_view["access"] == "read" and reader_view["shares"] is None
    for actor in ("stranger", "admin"):
        assert (await api.client.get(url, headers=scene.headers[actor])).status_code == 404
        listed = (await api.client.get(f"{PREFIX}/drawers", headers=scene.headers[actor])).json()
        assert str(scene.shared.id) not in str(listed)
    changes = [
        ("PATCH", url, {"name": "Renamed"}),
        ("PUT", f"{url}/shares/{UUID(int=1)}", {"level": "read"}),
        ("DELETE", f"{url}/shares/{UUID(int=1)}", None),
        ("DELETE", url, None),
    ]
    expected = {"reader": 403, "writer": 403, "stranger": 404, "admin": 404, "read_token": 403}
    for method, target, body in changes:
        for actor, status in {**expected, **DENIED}.items():
            response = await api.client.request(
                method, target, json=body, headers=scene.headers[actor]
            )
            assert response.status_code == status, (method, target, actor)
    renamed = await api.client.patch(url, json={"name": "Renamed"}, headers=scene.headers["owner"])
    assert renamed.status_code == 200
    not_empty = await api.client.delete(url, headers=scene.headers["owner"])
    assert not_empty.status_code == 409


# --- master data and users ----------------------------------------------------------------------


MASTER_DATA = {
    "contacts": {"name": "X"},
    "document-types": {"name": "X"},
    "tags": {"name": "X"},
    "attributes": {"name": "X", "data_type": "text"},
}
NOT_ADMIN = {"owner": 403, "reader": 403, "writer": 403, "stranger": 403, "read_token": 403}


@pytest.mark.parametrize("kind", MASTER_DATA)
async def test_master_data_is_read_by_all_and_changed_by_admins(api: Api, kind: str) -> None:
    scene = await build_scene(api)
    url = f"{PREFIX}/{kind}"
    for actor in ("owner", "reader", "stranger", "admin", "read_token"):
        assert (await api.client.get(url, headers=scene.headers[actor])).status_code == 200
    for actor in DENIED:
        assert (await api.client.get(url, headers=scene.headers[actor])).status_code == 401
    body = MASTER_DATA[kind]
    for actor, status in {**NOT_ADMIN, **DENIED}.items():
        response = await api.client.post(url, json=body, headers=scene.headers[actor])
        assert response.status_code == status, actor
    created = await api.client.post(url, json=body, headers=scene.headers["admin"])
    assert created.status_code == 201
    item = f"{url}/{created.json()['id']}"
    for actor in ("owner", "reader", "stranger", "read_token"):
        assert (await api.client.get(item, headers=scene.headers[actor])).status_code == 200
    for method, change in (("PATCH", {"name": "Y"}), ("DELETE", None)):
        for actor, status in {**NOT_ADMIN, **DENIED}.items():
            response = await api.client.request(
                method, item, json=change, headers=scene.headers[actor]
            )
            assert response.status_code == status, (method, actor)
    renamed = await api.client.patch(item, json={"name": "Y"}, headers=scene.headers["admin"])
    assert renamed.status_code == 200
    assert (await api.client.delete(item, headers=scene.headers["admin"])).status_code == 204


async def test_users_are_managed_by_admins(api: Api) -> None:
    scene = await build_scene(api)
    owner_list = (await api.client.get(f"{PREFIX}/users", headers=scene.headers["owner"])).json()
    assert all(set(user) == {"id", "username"} for user in owner_list)
    assert "deactivated" not in {user["username"] for user in owner_list}
    admin_list = (await api.client.get(f"{PREFIX}/users", headers=scene.headers["admin"])).json()
    assert {"role", "active"} <= set(admin_list[0])
    assert "deactivated" in {user["username"] for user in admin_list}
    target = f"{PREFIX}/users/{scene.owner.id}"
    for method, path, body in [
        ("POST", f"{PREFIX}/users", {"username": "new"}),
        ("PATCH", target, {}),
        ("PATCH", target, {"role": "admin"}),
        ("POST", f"{target}/password", {"password": "a long new password"}),
        ("DELETE", f"{target}/totp", None),
        ("DELETE", f"{target}/oidc", None),
        ("DELETE", target, None),
    ]:
        for actor, status in {**NOT_ADMIN, **DENIED}.items():
            response = await api.client.request(
                method, path, json=body, headers=scene.headers[actor]
            )
            assert response.status_code == status, (method, path, actor)
    account = (await api.client.get(target, headers=scene.headers["admin"])).json()
    assert account["has_password"] is True and account["totp_enabled"] is False
    summary = (await api.client.get(target, headers=scene.headers["reader"])).json()
    assert set(summary) == {"id", "username"}

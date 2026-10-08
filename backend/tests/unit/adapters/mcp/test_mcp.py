"""The MCP endpoint, with the SDK's own client: tokens, tools, and that the rights of the REST
API hold here too."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import pytest
from fastapi import FastAPI
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.memory import ManualClock
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.attributes import AttributeType, Money
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.ids import DocumentId, new_id
from papiq.core.services.objects import markdown_key
from tests import builders
from tests.api import auth, serving
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api, make_app
from tests.unit.adapters.rest.test_permission_matrix import Scene
from tests.unit.adapters.rest.test_resources import post
from tests.unit.adapters.rest.test_rules import upload
from tests.unit.adapters.rest.test_search import indexed_scene

URL = f"https://papiq{PREFIX}/mcp"
TEXT = "Strom Rechnung März\n" + "Zeile mit Inhalt\n" * 20


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[None]:
    """Run the app's lifespan in one task of its own (the SDK's task group must be entered and
    left in the same task, which a fixture's setup and teardown are not)."""
    ready, stop = asyncio.Event(), asyncio.Event()

    async def lifespan() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await asyncio.wait_for(ready.wait(), timeout=10)
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


@pytest.fixture
async def api() -> AsyncIterator[Api]:
    """The API with the MCP endpoint, its lifespan running."""

    clock = ManualClock(builders.NOW)
    container = build_memory_container(clock)
    services = build_services(container)
    builders.skip_classification(services.pipeline)
    app = make_app(container, services, mcp=True, mcp_text_max=100)
    async with running(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="https://papiq") as client:
            yield Api(container, services, app, client, clock)


@asynccontextmanager
async def connected(api: Api, headers: dict[str, str]) -> AsyncIterator[Client]:
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=api.app), base_url="https://papiq", headers=headers
    )
    async with http, Client(streamable_http_client(URL, http_client=http)) as client:
        yield client


async def call(api: Api, headers: dict[str, str], tool: str, **arguments: Any) -> CallToolResult:
    async with connected(api, headers) as client:
        return await client.call_tool(tool, arguments)


def data(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return dict(result.structured_content)


def error(result: CallToolResult) -> str:
    assert result.is_error
    return "".join(getattr(part, "text", "") for part in result.content)


@pytest.fixture
async def scene(api: Api) -> Scene:
    scene = await indexed_scene(api)
    for which in (scene.green, scene.yellow):
        await api.container.object_store.put(
            markdown_key(DocumentId(UUID(which))),
            TEXT.encode(),
            content_type="text/markdown",
        )
    return scene


# --- the endpoint -------------------------------------------------------------------------------


async def test_the_server_offers_five_tools(api: Api, scene: Scene) -> None:
    async with connected(api, scene.headers["owner"]) as client:
        tools = (await client.list_tools()).tools
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {"search", "get_document", "get_text", "update_metadata", "list_tags"}
    read_only = {
        name: tool.annotations is not None and tool.annotations.read_only_hint
        for name, tool in by_name.items()
    }
    assert read_only == {
        "search": True,
        "get_document": True,
        "get_text": True,
        "list_tags": True,
        "update_metadata": False,
    }
    assert "follow" in (by_name["get_text"].description or "")  # the warning about the text


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
async def test_both_spellings_of_the_path_work(api: Api, scene: Scene, path: str) -> None:
    response = await api.client.post(
        f"{PREFIX}{path}",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            **scene.headers["owner"],
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer papiq_nonsense"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
    ],
)
async def test_without_a_valid_token_the_answer_is_401(api: Api, headers: dict[str, str]) -> None:
    response = await api.client.post(
        f"{PREFIX}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**headers, "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("actor", ["expired_token", "revoked_token", "deactivated"])
async def test_tokens_that_are_no_longer_good_are_refused(
    api: Api, scene: Scene, actor: str
) -> None:
    response = await api.client.post(
        f"{PREFIX}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**scene.headers[actor], "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401


async def test_a_session_cookie_does_not_open_the_endpoint(api: Api, scene: Scene) -> None:
    async with api.sign_in(scene.owner) as session:
        response = await session.client.post(
            f"{PREFIX}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={**session.headers, "Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


async def test_the_endpoint_is_absent_when_switched_off() -> None:

    container = build_memory_container(ManualClock(builders.NOW))
    app = make_app(container, build_services(container), mcp=False)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://papiq"
    ) as client:
        response = await client.post(f"{PREFIX}/mcp", json={})
    assert response.status_code == 404


async def test_the_server_works_over_a_real_connection() -> None:

    clock = ManualClock(builders.NOW)
    container = build_memory_container(clock)
    services = build_services(container)
    builders.skip_classification(services.pipeline)
    app = make_app(container, services, mcp=True)  # Uvicorn runs its lifespan
    client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://papiq")
    api = Api(container, services, app, client, clock)
    user = await api.user()
    received = await services.pipeline.receive(
        user.id, builders.incoming((SAMPLES / "scan.pdf").read_bytes()), filename="a.pdf"
    )
    await api.drain()
    async with (
        serving(app) as base,
        httpx2.AsyncClient(headers=auth(user)) as http,
        Client(streamable_http_client(f"{base}{PREFIX}/mcp", http_client=http)) as mcp,
    ):
        result = await mcp.call_tool("get_document", {"id": str(received.id)})
    await client.aclose()
    assert data(result)["id"] == str(received.id)


# --- reading ------------------------------------------------------------------------------------


async def test_get_document_names_what_the_document_refers_to(api: Api, scene: Scene) -> None:
    master = api.services.master_data
    admin = await api.admin()
    contact = await master.create_contact(admin.id, "Stadtwerke")
    kind = await master.create_document_type(admin.id, "Rechnung")
    tag = await master.create_tag(admin.id, "Strom")
    amount = await master.create_attribute(admin.id, "Betrag", AttributeType.AMOUNT)

    await api.services.documents.change_metadata(
        scene.owner.id,
        DocumentId(UUID(scene.green)),
        DocumentChanges(
            contact_id=contact.id,
            document_type_id=kind.id,
            tag_ids=frozenset({tag.id}),
            attributes={amount.id: Money(Decimal("84.20"), "EUR")},
        ),
    )
    document = data(await call(api, scene.headers["reader"], "get_document", id=scene.green))
    assert document["access"] == "read"
    assert document["contact"] == {"id": str(contact.id), "name": "Stadtwerke"}
    assert document["document_type"]["name"] == "Rechnung"
    assert document["tags"] == [{"id": str(tag.id), "name": "Strom"}]
    assert document["attributes"] == {"Betrag": {"amount": "84.20", "currency": "EUR"}}
    assert document["lane"] == "green" and document["status"] == "completed"
    assert document["owner_id"] == str(scene.owner.id)


async def test_a_document_one_may_not_read_is_just_not_found(api: Api, scene: Scene) -> None:
    unknown = str(new_id())
    missing = error(await call(api, scene.headers["stranger"], "get_document", id=unknown))
    for actor, which in [
        ("stranger", scene.green),
        ("admin", scene.green),
        ("reader", scene.yellow),
        ("reader", scene.processing),
    ]:
        message = error(await call(api, scene.headers[actor], "get_document", id=which))
        assert message.replace(which, "X") == missing.replace(unknown, "X"), (actor, which)
    owner_sees = data(await call(api, scene.headers["owner"], "get_document", id=scene.yellow))
    assert owner_sees["lane"] == "yellow"


async def test_get_text_reads_in_pieces_and_only_for_readers(api: Api, scene: Scene) -> None:
    first = data(await call(api, scene.headers["reader"], "get_text", id=scene.green))
    assert first["text"] == TEXT[:100]  # the server's limit: PAPIQ_MCP_TEXT_MAX
    assert (first["offset"], first["total_length"], first["next_offset"]) == (0, len(TEXT), 100)
    asked_more = data(
        await call(api, scene.headers["reader"], "get_text", id=scene.green, limit=5000)
    )
    assert asked_more["text"] == TEXT[:100]
    second = data(
        await call(api, scene.headers["reader"], "get_text", id=scene.green, offset=100, limit=10)
    )
    assert second["text"] == TEXT[100:110] and second["next_offset"] == 110
    end = data(
        await call(api, scene.headers["reader"], "get_text", id=scene.green, offset=len(TEXT) - 3)
    )
    assert end["text"] == TEXT[-3:] and end["next_offset"] is None
    for actor, which in [("stranger", scene.green), ("reader", scene.yellow)]:
        assert "not found" in error(await call(api, scene.headers[actor], "get_text", id=which))
    bad = await call(api, scene.headers["reader"], "get_text", id=scene.green, offset=-1)
    assert bad.is_error


async def test_a_document_without_text_says_so(api: Api, scene: Scene) -> None:
    await api.container.object_store.delete(markdown_key(DocumentId(UUID(scene.green))))
    result = await call(api, scene.headers["owner"], "get_text", id=scene.green)
    assert "no text yet" in error(result)


async def test_list_tags(api: Api, scene: Scene) -> None:
    admin = await api.admin()
    for name in ("Zeta", "alpha"):
        await api.services.master_data.create_tag(admin.id, name)
    tags = data(await call(api, scene.headers["read_token"], "list_tags"))["tags"]
    assert [tag["name"] for tag in tags] == ["alpha", "Zeta"]


# --- search -------------------------------------------------------------------------------------


async def found(api: Api, headers: dict[str, str], **arguments: Any) -> set[str]:
    ids: set[str] = set()
    offset: int | None = 0
    while offset is not None:
        page = data(
            await call(api, headers, "search", query="pdf", limit=1, offset=offset, **arguments)
        )
        ids |= {item["id"] for item in page["items"]}
        offset = page["next_offset"]
    return ids


async def test_search_finds_what_the_caller_may_read(api: Api, scene: Scene) -> None:
    everything = {scene.green, scene.yellow, scene.processing}
    assert await found(api, scene.headers["owner"]) == everything
    assert await found(api, scene.headers["read_token"]) == everything
    assert await found(api, scene.headers["reader"]) == {scene.green}
    assert await found(api, scene.headers["stranger"]) == set()
    page = data(await call(api, scene.headers["reader"], "search", query="pdf"))
    item = page["items"][0]
    assert item["id"] == scene.green and item["access"] == "read"
    assert set(item) >= {"title", "score", "snippet", "contact", "document_type", "tags"}
    assert isinstance(item["snippet"], str)


async def test_search_filters_by_name(api: Api, scene: Scene) -> None:
    admin = await api.admin()
    master = api.services.master_data
    tag = await master.create_tag(admin.id, "Strom")

    await api.services.documents.change_metadata(
        scene.owner.id,
        DocumentId(UUID(scene.green)),
        DocumentChanges(tag_ids=frozenset({tag.id})),
    )
    await api.services.indexing.reconcile()  # type: ignore[union-attr]
    while await api.services.indexing.run_next_job():  # type: ignore[union-attr]
        pass
    assert await found(api, scene.headers["owner"], tags=["strom"]) == {scene.green}
    unknown = await call(api, scene.headers["owner"], "search", query="pdf", tags=["Nope"])
    assert "no tag named 'Nope'" in error(unknown)
    unknown = await call(api, scene.headers["owner"], "search", query="pdf", contact="Nope")
    assert "no contact named 'Nope'" in error(unknown)


async def test_search_without_an_index_says_so() -> None:

    container = build_memory_container(ManualClock(builders.NOW))
    services = replace(build_services(container), search=None)
    app = make_app(container, services, mcp=True)
    async with running(app):
        api = Api(
            container,
            services,
            app,
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://papiq"),
            ManualClock(builders.NOW),
        )
        user = await api.user()
        result = await call(api, auth(user), "search", query="anything")
    assert "search is not available" in error(result)


# --- changing -----------------------------------------------------------------------------------


async def test_update_metadata_changes_what_is_given(api: Api, scene: Scene) -> None:
    admin = await api.admin()
    master = api.services.master_data
    contact = await master.create_contact(admin.id, "Stadtwerke")
    await master.create_tag(admin.id, "Strom")
    await master.create_tag(admin.id, "Wohnung")
    await master.create_attribute(admin.id, "Zählerstand", AttributeType.NUMBER)
    result = data(
        await call(
            api,
            scene.headers["writer"],
            "update_metadata",
            id=scene.green,
            title="Strom März",
            contact="stadtwerke",
            tags=["Strom", "wohnung"],
            document_date="2026-03-31",
            attributes={"zählerstand": "1234.5"},
        )
    )
    document = result["document"]
    assert result["access"] == "read_write" and document["title"] == "Strom März"
    assert document["contact"]["id"] == str(contact.id)
    assert [tag["name"] for tag in document["tags"]] == ["Strom", "Wohnung"]
    assert document["document_date"] == "2026-03-31"
    assert document["attributes"] == {"Zählerstand": "1234.5"}

    # What is left out stays; tags are replaced as a whole; null removes.
    again = data(
        await call(
            api,
            scene.headers["owner"],
            "update_metadata",
            id=scene.green,
            tags=["Strom"],
            contact=None,
            document_date=None,
            attributes={"Zählerstand": None},
        )
    )["document"]
    assert again["title"] == "Strom März"
    assert again["contact"] is None and again["document_date"] is None
    assert [tag["name"] for tag in again["tags"]] == ["Strom"] and again["attributes"] == {}
    # The same change as over REST.
    rest = (
        await api.client.get(f"{PREFIX}/documents/{scene.green}", headers=auth(scene.owner))
    ).json()
    assert rest["title"] == "Strom März" and rest["contact_id"] is None


async def test_update_metadata_needs_a_read_write_token_and_write_access(
    api: Api, scene: Scene
) -> None:
    refused = await call(
        api, scene.headers["read_token"], "update_metadata", id=scene.green, title="X"
    )
    assert "may only read" in error(refused)
    no_write = await call(
        api, scene.headers["reader"], "update_metadata", id=scene.green, title="X"
    )
    assert "no write access" in error(no_write)
    hidden = await call(
        api, scene.headers["stranger"], "update_metadata", id=scene.green, title="X"
    )
    assert "not found" in error(hidden)
    unchanged = (
        await api.client.get(f"{PREFIX}/documents/{scene.green}", headers=auth(scene.owner))
    ).json()
    assert unchanged["title"] != "X"


async def test_update_metadata_refuses_unknown_names_and_bad_values(api: Api, scene: Scene) -> None:
    admin = await api.admin()
    await api.services.master_data.create_attribute(admin.id, "Betrag", AttributeType.AMOUNT)
    owner = scene.headers["owner"]
    cases: list[tuple[dict[str, Any], str]] = [
        ({"contact": "Nobody"}, "no contact named 'Nobody'"),
        ({"document_type": "Nothing"}, "no document type named 'Nothing'"),
        ({"tags": ["Missing"]}, "no tag named 'Missing'"),
        ({"attributes": {"Unknown": 1}}, "no attribute named 'Unknown'"),
        ({"attributes": {"Betrag": "lots"}}, "does not accept"),
        ({"title": ""}, ""),
    ]
    for arguments, message in cases:
        result = await call(api, owner, "update_metadata", id=scene.green, **arguments)
        assert message in error(result), arguments


async def test_update_metadata_returns_the_document_and_the_rules_report(
    api: Api, scene: Scene
) -> None:
    result = data(
        await call(api, scene.headers["owner"], "update_metadata", id=scene.green, title="Z")
    )
    assert result["id"] == scene.green and result["document"]["title"] == "Z"
    assert result["rules"] == [] or result["rules"] is None


async def test_an_editor_whose_change_files_the_document_away_learns_nothing_more(
    api: Api,
) -> None:
    admin, owner, editor = await api.admin(), await api.user(), await api.user()
    o = auth(owner)
    acme = await post(api, "/contacts", {"name": "ACME"}, auth(admin))
    shared = await post(api, "/drawers", {"name": "Shared"}, o)
    private = await post(api, "/drawers", {"name": "Private"}, o)
    response = await api.client.put(
        f"{PREFIX}/drawers/{shared['id']}/shares/{editor.id}",
        json={"level": "read_write"},
        headers=o,
    )
    assert response.status_code < 300, response.text
    for name, triggers, condition, drawer in (
        ("Shared", ["ingest"], {"field": "channel", "op": "is", "value": "api"}, shared),
        ("Private", ["change"], {"field": "contact", "op": "is", "value": acme["id"]}, private),
    ):
        await post(
            api,
            "/rules",
            {
                "name": name,
                "triggers": triggers,
                "conditions": {"all": [condition]},
                "actions": [{"type": "set_drawer", "drawer_id": drawer["id"]}],
            },
            o,
        )
    id = await upload(api, o)

    result = await call(api, auth(editor), "update_metadata", id=id, contact="ACME")

    assert data(result) == {"id": id, "access": None, "document": None, "rules": None}
    assert "not found" in error(await call(api, auth(editor), "get_document", id=id))
    owners = data(await call(api, o, "update_metadata", id=id, title="Seen by the owner"))
    assert owners["document"]["drawer_id"] == private["id"]
    assert [rule["name"] for rule in owners["rules"] or []] == []  # no change that triggers it

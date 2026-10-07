"""Server-sent events through a real Uvicorn server: each user receives only events of
documents they may read."""

import asyncio
from datetime import timedelta

import httpx2

from papiq.adapters.inbound.rest import PREFIX, close_event_streams
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.drawers import ShareLevel
from tests.api import Stream, auth, issue_token, listen, serving, until
from tests.builders import PASSWORD
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api, make_app

EVENTS = f"{PREFIX}/events"


async def test_events_reach_only_users_who_may_read_the_document(api: Api) -> None:
    owner, reader, stranger = await api.user(), await api.user(), await api.user()
    shared = await api.services.drawers.create(owner.id, "Shared")
    await api.services.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)

    async with serving(api.app) as url, httpx2.AsyncClient(base_url=url, timeout=10) as client:
        streams = {user.id: Stream() for user in (owner, reader, stranger)}
        tasks = [
            asyncio.create_task(listen(client, user, streams[user.id]))
            for user in (owner, reader, stranger)
        ]
        for stream in streams.values():
            await asyncio.wait_for(stream.ready.wait(), timeout=5)
            assert stream.status == 200
        await asyncio.sleep(0.05)  # streams are registered once the first byte is out

        files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
        response = await client.post(
            f"{PREFIX}/documents",
            files=files,
            data={"drawer_id": str(shared.id)},
            headers=auth(owner),
        )
        document = response.json()["id"]
        own = streams[owner.id]
        await until(lambda: "document.received" in own.types())
        await api.drain()  # processing runs to green
        await until(lambda: own.events and own.events[-1]["type"] == "document.lane_changed")

        assert own.types()[:2] == ["document.received", "document.step_completed"]
        assert own.events[-1]["new"] == "green"
        assert {event["document_id"] for event in own.events} == {document}
        step = own.events[1]
        assert (step["step"], step["run"], step["outcome"]) == ("receive", 1, "ok")

        shared_events = streams[reader.id].types()
        assert "document.received" not in shared_events  # not visible while processing
        assert shared_events[-1] == "document.lane_changed"  # visible once green
        assert streams[stranger.id].events == []

        close_event_streams(api.app)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)


async def test_a_stream_for_one_document(api: Api) -> None:
    owner, stranger = await api.user(), await api.user()
    async with serving(api.app) as url, httpx2.AsyncClient(base_url=url, timeout=10) as client:
        files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
        first = (await client.post(f"{PREFIX}/documents", files=files, headers=auth(owner))).json()
        other = {"file": ("photo.jpg", (SAMPLES / "photo.jpg").read_bytes(), "x/y")}
        await asyncio.sleep(0.1)  # the received events of both are delivered before listening

        hidden = await client.get(
            EVENTS, params={"document_id": first["id"]}, headers=auth(stranger)
        )
        assert hidden.status_code == 404

        stream = Stream()
        task = asyncio.create_task(listen(client, owner, stream, document_id=first["id"]))
        await asyncio.wait_for(stream.ready.wait(), timeout=5)
        await asyncio.sleep(0.05)
        await client.post(f"{PREFIX}/documents", files=other, headers=auth(owner))
        await api.drain()
        await until(lambda: stream.types().count("document.lane_changed") == 1)
        assert {event["document_id"] for event in stream.events} == {first["id"]}
        close_event_streams(api.app)
        await asyncio.wait_for(task, timeout=5)


async def test_a_stream_ends_when_its_token_is_revoked() -> None:
    container = build_memory_container()
    services = build_services(container)
    app = make_app(container, services, recheck=timedelta(milliseconds=200))
    user = await services.users.bootstrap_admin("root", PASSWORD)
    assert user is not None
    await issue_token(services.auth, user)
    [token] = await services.auth.list_api_tokens(user.id)
    async with serving(app) as url, httpx2.AsyncClient(base_url=url, timeout=10) as client:
        stream = Stream()
        task = asyncio.create_task(listen(client, user, stream))
        await asyncio.wait_for(stream.ready.wait(), timeout=5)
        assert stream.status == 200
        await services.auth.revoke_api_token(user.id, token.id)
        await asyncio.wait_for(task, timeout=5)  # the server ended the stream

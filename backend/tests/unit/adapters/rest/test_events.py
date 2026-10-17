"""Server-sent events through a real Uvicorn server: each user receives only events of
documents they may read."""

import asyncio
from datetime import timedelta

import httpx2

from papiq.adapters.inbound.rest import PREFIX, close_event_streams
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.events import ClassifierTrained
from papiq.core.domain.spaces import ShareLevel
from tests.api import Stream, auth, issue_token, listen, serving, until
from tests.builders import NOW, PASSWORD
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api, make_app

EVENTS = f"{PREFIX}/events"


async def test_events_reach_only_users_who_may_read_the_document(api: Api) -> None:
    owner, reader, stranger = await api.user(), await api.user(), await api.user()
    admin = await api.admin()
    shared = await api.services.spaces.create(owner.id, "Shared")
    await api.services.spaces.share(owner.id, shared.id, reader.id, ShareLevel.READ)

    async with serving(api.app) as url, httpx2.AsyncClient(base_url=url, timeout=10) as client:
        streams = {user.id: Stream() for user in (owner, reader, stranger, admin)}
        tasks = [
            asyncio.create_task(listen(client, user, streams[user.id]))
            for user in (owner, reader, stranger, admin)
        ]
        for stream in streams.values():
            await asyncio.wait_for(stream.ready.wait(), timeout=5)
            assert stream.status == 200
        await asyncio.sleep(0.05)  # streams are registered once the first byte is out

        files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
        response = await client.post(
            f"{PREFIX}/documents",
            files=files,
            data={"space_id": str(shared.id)},
            headers=auth(owner),
        )
        document = response.json()["id"]
        own = streams[owner.id]
        await until(lambda: "document.received" in own.types())
        await api.drain()  # processing runs to green
        await until(lambda: own.events and own.events[-1]["type"] == "document.lane_changed")

        assert own.types()[:2] == ["document.received", "document.node_completed"]
        assert own.events[-1]["new"] == "green"
        assert {event["document_id"] for event in own.events} == {document}
        node = own.events[1]
        assert (node["node"], node["run"], node["outcome"]) == ("receive", 1, "ok")

        shared_events = streams[reader.id].types()
        assert "document.received" not in shared_events  # not visible while processing
        assert shared_events[-1] == "document.lane_changed"  # visible once green
        assert streams[stranger.id].events == []
        # Admins read every document, in processing too (Tobi, 08.10.2026).
        await until(lambda: streams[admin.id].types() == own.types())

        # A deleted document cannot be checked any more: those who could read it hear of it.
        deleted = await client.delete(f"{PREFIX}/documents/{document}", headers=auth(owner))
        assert deleted.status_code == 204
        await until(lambda: "document.deleted" in own.types())
        await until(lambda: "document.deleted" in streams[reader.id].types())
        await until(lambda: "document.deleted" in streams[admin.id].types())
        await asyncio.sleep(0.1)
        assert own.events[-1] == {**own.events[-1], "type": "document.deleted"}
        assert "readers" not in own.events[-1] and "readers" not in streams[reader.id].events[-1]
        assert streams[stranger.id].events == []

        close_event_streams(api.app)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)


async def test_the_end_of_a_training_reaches_only_admins(api: Api) -> None:
    user, admin = await api.user(), await api.admin()
    async with serving(api.app) as url, httpx2.AsyncClient(base_url=url, timeout=10) as client:
        streams = {member.id: Stream() for member in (user, admin)}
        tasks = [
            asyncio.create_task(listen(client, member, streams[member.id]))
            for member in (user, admin)
        ]
        for stream in streams.values():
            await asyncio.wait_for(stream.ready.wait(), timeout=5)
        await asyncio.sleep(0.05)
        async with api.container.unit_of_work() as uow:
            await uow.outbox.add([ClassifierTrained(occurred_at=NOW, number=4, examples=120)])
            await uow.commit()
        await until(lambda: streams[admin.id].types() == ["classifier.trained"])
        event = streams[admin.id].events[0]
        assert (event["number"], event["examples"]) == (4, 120)
        assert "document_id" not in event
        await asyncio.sleep(0.1)
        assert streams[user.id].events == []
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

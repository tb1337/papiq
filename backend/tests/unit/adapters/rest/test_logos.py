"""Logos of contacts over HTTP: upload (admins, 1 MiB, PNG, JPEG or WebP; SVG refused),
removal, and reading by every user with a cache header of its own."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import httpx2
import pytest
from httpx2 import Response

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.memory import FAKE_LOGO, FakeLogoFetcher, ManualClock
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.ids import ContactId
from papiq.core.services.logos import LOGO_MAX_SIZE
from papiq.core.services.objects import logo_key
from tests import builders
from tests.api import auth
from tests.contracts.images import SVG, image
from tests.unit.adapters.rest.conftest import Api, make_app

PNG = image((180, 120))


async def upload(api: Api, contact: str, data: bytes, headers: dict[str, str]) -> Response:
    files = {"file": ("logo", data, "application/octet-stream")}
    return await api.client.put(f"{PREFIX}/contacts/{contact}/logo", files=files, headers=headers)


async def contact_of(api: Api, headers: dict[str, str]) -> str:
    response = await api.client.post(f"{PREFIX}/contacts", json={"name": "Nord"}, headers=headers)
    assert response.status_code == 201, response.text
    assert (response.json()["logo"], response.json()["logo_failure"]) == (None, None)
    return str(response.json()["id"])


async def test_an_uploaded_logo_is_read_by_everyone_and_may_be_cached(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    a = auth(admin)
    contact = await contact_of(api, a)
    response = await upload(api, contact, PNG, a)
    assert response.status_code == 200, response.text
    logo = response.json()["logo"]
    assert (logo["source"], logo["domain"]) == ("uploaded", None)
    assert logo["url"].startswith(f"{PREFIX}/contacts/{contact}/logo?v=")
    assert await api.container.object_store.get(logo_key(ContactId(UUID(contact)))) == FAKE_LOGO

    read = await api.client.get(logo["url"], headers=auth(user))
    assert read.status_code == 200
    assert read.content == FAKE_LOGO
    assert read.headers["content-type"] == "image/webp"
    assert read.headers["cache-control"] == "private, max-age=86400"
    assert read.headers["x-content-type-options"] == "nosniff"
    listed = await api.client.get(f"{PREFIX}/contacts", headers=auth(user))
    assert listed.json()[0]["logo"] == logo

    # Another logo is another URL.
    api.clock.advance(timedelta(seconds=5))
    again = await upload(api, contact, image((64, 64), "JPEG"), a)
    assert again.json()["logo"]["url"] != logo["url"]


async def test_a_logo_is_removed_and_the_contact_shows_its_initials(api: Api) -> None:
    admin = await api.admin()
    a = auth(admin)
    contact = await contact_of(api, a)
    await upload(api, contact, image((64, 64), "WEBP"), a)
    removed = await api.client.delete(f"{PREFIX}/contacts/{contact}/logo", headers=a)
    assert removed.status_code == 204
    after = await api.client.get(f"{PREFIX}/contacts/{contact}", headers=a)
    assert after.json()["logo"] is None
    missing = await api.client.get(f"{PREFIX}/contacts/{contact}/logo", headers=a)
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert not await api.container.object_store.exists(logo_key(ContactId(UUID(contact))))
    # Removing again does nothing; an unknown contact is not found.
    assert (
        await api.client.delete(f"{PREFIX}/contacts/{contact}/logo", headers=a)
    ).status_code == 204
    unknown = await api.client.delete(f"{PREFIX}/contacts/{UUID(int=3)}/logo", headers=a)
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    ("data", "status"),
    [
        (SVG, 415),
        (b"GIF89a" + bytes(200), 415),
        (image((32, 32), "ICO"), 415),  # an icon of a website, not an upload
        (b"%PDF-1.7\n" + bytes(200), 415),
        (PNG[:40], 422),  # cut off: cannot be read
        (b"\x89PNG\r\n\x1a\n" + bytes(LOGO_MAX_SIZE - 8), 200),  # at the limit
        (b"\x89PNG\r\n\x1a\n" + bytes(LOGO_MAX_SIZE - 7), 413),
    ],
    ids=["svg", "gif", "ico", "pdf", "damaged", "at-limit", "too-large"],
)
async def test_only_small_png_jpeg_or_webp_images_are_taken(
    api: Api, data: bytes, status: int
) -> None:
    admin = await api.admin()
    contact = await contact_of(api, auth(admin))
    response = await upload(api, contact, data, auth(admin))
    assert response.status_code == status, response.text
    if status != 200:
        stored = await api.client.get(f"{PREFIX}/contacts/{contact}", headers=auth(admin))
        assert stored.json()["logo"] is None


async def test_only_admins_upload_and_remove_logos(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    contact = await contact_of(api, auth(admin))
    refused = await upload(api, contact, PNG, auth(user))
    assert refused.status_code == 403
    await upload(api, contact, PNG, auth(admin))
    kept = await api.client.delete(f"{PREFIX}/contacts/{contact}/logo", headers=auth(user))
    assert kept.status_code == 403
    anonymous = await api.client.get(f"{PREFIX}/contacts/{contact}/logo")
    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "no-store"


async def test_a_logo_goes_with_its_contact(api: Api) -> None:
    admin = await api.admin()
    contact = await contact_of(api, auth(admin))
    await upload(api, contact, PNG, auth(admin))
    deleted = await api.client.delete(f"{PREFIX}/contacts/{contact}", headers=auth(admin))
    assert deleted.status_code == 204
    assert not await api.container.object_store.exists(logo_key(ContactId(UUID(contact))))


@pytest.fixture
async def fetching() -> AsyncIterator[Api]:
    """The API with fetching on, against a stand-in for the websites."""
    clock = ManualClock(builders.NOW)
    fetcher = FakeLogoFetcher({"nord.example": PNG})
    container = replace(build_memory_container(clock), logo_fetcher=fetcher)
    services = build_services(container)
    app = make_app(container, services)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://papiq") as client:
        yield Api(container, services, app, client, clock)


async def test_an_admin_has_the_logo_fetched(fetching: Api) -> None:
    api = fetching
    admin, user = await api.admin(), await api.user()
    a = auth(admin)
    pipeline = await api.client.get(f"{PREFIX}/pipeline", headers=auth(user))
    assert pipeline.json()["logo_fetch"] is True
    body = {"name": "Nord", "identifiers": [{"kind": "email_domain", "value": "nord.example"}]}
    contact = (await api.client.post(f"{PREFIX}/contacts", json=body, headers=a)).json()["id"]
    # Getting a domain queued the fetch already; the worker runs it.
    assert await api.services.logos.run_next_job()
    logo = (await api.client.get(f"{PREFIX}/contacts/{contact}", headers=a)).json()["logo"]
    assert (logo["source"], logo["domain"]) == ("fetched", "nord.example")

    url = f"{PREFIX}/contacts/{contact}/logo/fetch"
    refused = await api.client.post(url, headers=auth(user))
    assert refused.status_code == 403
    asked = await api.client.post(url, headers=a)
    assert (asked.status_code, asked.content) == (202, b"")
    assert await api.services.logos.run_next_job()

    await upload(api, contact, PNG, a)
    uploaded = await api.client.post(url, headers=a)
    assert uploaded.status_code == 409, uploaded.text
    bare = await api.client.post(f"{PREFIX}/contacts", json={"name": "Bare"}, headers=a)
    without = await api.client.post(f"{PREFIX}/contacts/{bare.json()['id']}/logo/fetch", headers=a)
    assert without.status_code == 422, without.text


async def test_without_fetching_the_button_is_unavailable(api: Api) -> None:
    admin = await api.admin()
    contact = await contact_of(api, auth(admin))
    pipeline = await api.client.get(f"{PREFIX}/pipeline", headers=auth(admin))
    assert pipeline.json()["logo_fetch"] is False
    response = await api.client.post(f"{PREFIX}/contacts/{contact}/logo/fetch", headers=auth(admin))
    assert response.status_code == 503, response.text

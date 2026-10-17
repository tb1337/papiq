"""Logos of contacts: uploaded by admins, read by everyone, gone with their contact; the file
and the contact always tell the same logo."""

import asyncio
from datetime import timedelta

import pytest

from papiq.adapters.outbound.memory import (
    FAKE_LOGO,
    FakeImageNormaliser,
    FakeLogoFetcher,
    MemoryObjectStore,
    MemoryUnitOfWork,
)
from papiq.core.domain.appearance import ContactLogo, FailedLogoFetch, LogoSource
from papiq.core.domain.errors import (
    ConflictError,
    ImageError,
    LogoFetchDisabledError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.ids import ContactId, UserId, new_id
from papiq.core.domain.jobs import JobStatus
from papiq.core.domain.master_data import Contact
from papiq.core.domain.users import Role
from papiq.core.ports.logos import FetchedLogo
from papiq.core.services.logos import FETCHING_OFF, LOGO_JOB, LOGO_MAX_SIZE, LogoService
from papiq.core.services.master_data import replace_learned_identifiers
from papiq.core.services.objects import logo_key
from tests.builders import NOW
from tests.contracts.images import SVG, image
from tests.unit.services.conftest import World

PNG = image((200, 100))


async def test_an_admin_uploads_a_logo_everyone_reads(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    contact = await world.master_data.create_contact(admin.id, "Nord")
    stored = await world.logos.upload(admin.id, contact.id, PNG)
    assert stored.logo is not None
    assert (stored.logo.source, stored.logo.stored_at, stored.logo.domain) == (
        LogoSource.UPLOADED,
        NOW,
        None,
    )
    assert await world.object_store.get(logo_key(contact.id)) == FAKE_LOGO
    read = await world.logos.logo(user.id, contact.id)
    assert (read.data, read.logo) == (FAKE_LOGO, stored.logo)
    with pytest.raises(PermissionDeniedError):
        await world.logos.upload(user.id, contact.id, PNG)
    with pytest.raises(PermissionDeniedError):
        await world.logos.remove(user.id, contact.id)

    removed = await world.logos.remove(admin.id, contact.id)
    assert removed.logo is None
    assert not await world.object_store.exists(logo_key(contact.id))
    with pytest.raises(NotFoundError):
        await world.logos.logo(user.id, contact.id)


async def test_what_is_no_logo_changes_nothing(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Nord")
    for data, error in (
        (SVG, UnsupportedMediaTypeError),
        (image((40, 40), "ICO"), UnsupportedMediaTypeError),
        (PNG[:30], ImageError),
        (b"\x89PNG\r\n\x1a\n" + bytes(LOGO_MAX_SIZE), ValidationError),
    ):
        with pytest.raises(error):
            await world.logos.upload(admin.id, contact.id, data)
    async with world.uow() as uow:
        assert (await uow.contacts.get(contact.id)).logo is None
    assert not await world.object_store.exists(logo_key(contact.id))
    with pytest.raises(NotFoundError):
        await world.logos.upload(admin.id, ContactId(new_id()), PNG)


async def test_a_logo_goes_with_its_contact_also_while_one_is_stored(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Nord")
    # An upload and the deletion at once: whichever comes second sees the first's result, so
    # no file stays behind without its contact.
    upload, deletion = await asyncio.gather(
        world.logos.upload(admin.id, contact.id, PNG),
        world.master_data.delete_contact(admin.id, contact.id),
        return_exceptions=True,
    )
    assert deletion is None
    assert not isinstance(upload, Exception) or isinstance(upload, NotFoundError)
    assert not await world.object_store.exists(logo_key(contact.id))
    async with world.uow() as uow:
        assert await uow.contacts.find(contact.id) is None


# --- fetching ---------------------------------------------------------------------------------

DOMAIN = Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example")
IBAN = Identifier(IdentifierKind.IBAN, "DE89370400440532013000")


async def queued(world: World) -> list[dict[str, object]]:
    return [
        dict(job.payload)
        for job in world.database.jobs.values()
        if job.kind == LOGO_JOB and job.status is JobStatus.QUEUED
    ]


async def run_all(logos: LogoService) -> int:
    count = 0
    while await logos.run_next_job():
        count += 1
    return count


async def stored(world: World, id: ContactId) -> Contact:
    async with world.uow() as uow:
        return await uow.contacts.get(id)


async def test_a_new_domain_queues_the_fetch_of_the_logo(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher({"nord.example": PNG})
    master, logos = world.fetching(fetcher)
    nord = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN, IBAN])
    plain = await master.create_contact(
        admin.id, "Süd", identifiers=[Identifier(IdentifierKind.CUSTOMER_NUMBER, "4711")]
    )
    assert await queued(world) == [
        {"contact_id": str(nord.id), "domain": "nord.example", "asked": False}
    ]
    assert await run_all(logos) == 1
    assert fetcher.calls == ["nord.example"]
    logo = (await stored(world, nord.id)).logo
    assert logo == ContactLogo(source=LogoSource.FETCHED, stored_at=NOW, domain="nord.example")
    assert await world.object_store.get(logo_key(nord.id)) == FAKE_LOGO

    # A rename, another identifier: nothing to fetch.
    await master.change_contact(admin.id, nord.id, name="Nord AG")
    await master.change_contact(admin.id, nord.id, identifiers=[DOMAIN, IBAN])
    # Attached from a document, a domain of a contact without a logo.
    attached = Identifier(IdentifierKind.EMAIL_DOMAIN, "sued.example")
    await master.add_identifier(admin.id, plain.id, attached)
    assert await queued(world) == [
        {"contact_id": str(plain.id), "domain": "sued.example", "asked": False}
    ]


async def test_a_fetched_logo_is_fetched_again_for_a_new_domain(world: World) -> None:
    """By hand, attached or learned; a domain that finds none leaves the logo, and an uploaded
    logo is never replaced."""
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher({"nord.example": PNG, "nord-ag.example": image((300, 300))})
    master, logos = world.fetching(fetcher)
    nord = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await run_all(logos)
    first = (await stored(world, nord.id)).logo
    assert first is not None and first.domain == "nord.example"
    world.clock.advance(timedelta(minutes=1))
    nothing = Identifier(IdentifierKind.EMAIL_DOMAIN, "n.example")
    await master.change_contact(admin.id, nord.id, identifiers=[DOMAIN, nothing])
    assert await queued(world) == [
        {"contact_id": str(nord.id), "domain": "n.example", "asked": False}
    ]
    await run_all(logos)
    kept = await stored(world, nord.id)
    assert kept.logo == first and kept.logo_failure is not None
    attached = Identifier(IdentifierKind.EMAIL_DOMAIN, "nord-ag.example")
    await master.add_identifier(admin.id, nord.id, attached)
    await run_all(logos)
    assert fetcher.calls == ["nord.example", "n.example", "nord-ag.example"]
    again = (await stored(world, nord.id)).logo
    assert again == ContactLogo(
        source=LogoSource.FETCHED, stored_at=world.clock.now(), domain="nord-ag.example"
    )
    # Learned by training, the same: queued for the new domain.
    async with world.uow() as uow:
        learned = [Identifier(IdentifierKind.EMAIL_DOMAIN, "nord-gruppe.example")]
        await replace_learned_identifiers(uow, {nord.id: learned}, fetch_logos_at=NOW)
        await uow.commit()
    assert await queued(world) == [
        {"contact_id": str(nord.id), "domain": "nord-gruppe.example", "asked": False}
    ]
    await run_all(logos)
    # The domain the logo came from, taken away and entered again: nothing new to fetch.
    await master.change_contact(admin.id, nord.id, identifiers=[DOMAIN])
    await master.change_contact(admin.id, nord.id, identifiers=[DOMAIN, attached])
    assert await queued(world) == []
    # An uploaded logo stays whatever domain comes.
    await logos.upload(admin.id, nord.id, PNG)
    other = Identifier(IdentifierKind.EMAIL_DOMAIN, "nord-neu.example")
    await master.add_identifier(admin.id, nord.id, other)
    assert await queued(world) == []
    # fetch-logos still asks only for contacts without a logo.
    assert await logos.request_all() == 0


async def test_a_fetch_queued_before_fetching_was_switched_off_ends_at_once(
    world: World,
) -> None:
    admin = await world.user(role=Role.ADMIN)
    master, _ = world.fetching(FakeLogoFetcher({"nord.example": PNG}))
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    (job,) = [job for job in world.database.jobs.values() if job.kind == LOGO_JOB]
    assert await world.logos.run_next_job() is True  # without fetching now
    ended = world.database.jobs[job.id]
    assert (ended.status, ended.last_error, ended.attempts) == (
        JobStatus.FAILED,
        FETCHING_OFF,
        1,
    )
    assert await world.logos.run_next_job() is False
    assert (await stored(world, contact.id)).logo is None


async def test_without_fetching_nothing_is_queued(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await world.master_data.add_identifier(
        admin.id, contact.id, Identifier(IdentifierKind.EMAIL_DOMAIN, "n.example")
    )
    assert await queued(world) == []
    assert not world.logos.fetching
    assert await world.logos.run_next_job() is False
    with pytest.raises(LogoFetchDisabledError):
        await world.logos.request_fetch(admin.id, contact.id)
    with pytest.raises(LogoFetchDisabledError):
        await world.logos.request_all()


async def test_a_failed_fetch_is_noted_and_tried_again_only_for_another_domain(
    world: World,
) -> None:
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher({"n.example": PNG})
    master, logos = world.fetching(fetcher)
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await run_all(logos)
    failed = await stored(world, contact.id)
    assert failed.logo is None
    assert failed.logo_failure == FailedLogoFetch(
        domains=frozenset({"nord.example"}), failed_at=NOW
    )
    # The same domain again (taken away and entered anew), a run of fetch-logos: no new try.
    await master.change_contact(admin.id, contact.id, identifiers=[])
    await master.change_contact(admin.id, contact.id, identifiers=[DOMAIN])
    assert await logos.request_all() == 0
    assert await queued(world) == []
    # Another domain is tried.
    await master.add_identifier(
        admin.id, contact.id, Identifier(IdentifierKind.EMAIL_DOMAIN, "n.example")
    )
    await run_all(logos)
    assert fetcher.calls == ["nord.example", "n.example"]
    found = await stored(world, contact.id)
    assert found.logo is not None and found.logo.domain == "n.example"
    assert found.logo_failure is None


async def test_fetch_logos_tries_no_domain_twice(world: World) -> None:
    """Two failing domains are each tried once, not in turn on every run."""
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher()
    master = world.master_data  # entered without fetching on
    other = Identifier(IdentifierKind.EMAIL_DOMAIN, "zz-nord.example")
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN, other])
    _, logos = world.fetching(fetcher)
    for _ in range(4):
        await logos.request_all()
        await run_all(logos)
    assert fetcher.calls == ["nord.example", "zz-nord.example"]
    failed = (await stored(world, contact.id)).logo_failure
    assert failed is not None and failed.domains == {"nord.example", "zz-nord.example"}
    # An admin's request tries them again, all of them.
    await logos.request_fetch(admin.id, contact.id)
    await run_all(logos)
    assert fetcher.calls[2:] == ["nord.example", "zz-nord.example"]


async def test_training_that_learns_a_domain_queues_the_fetch(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    master, _ = world.fetching(FakeLogoFetcher())
    contact = await master.create_contact(admin.id, "Nord")
    async with world.uow() as uow:
        await replace_learned_identifiers(
            uow, {contact.id: [Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example")]}
        )
        await uow.commit()
    assert await queued(world) == []  # without fetching
    async with world.uow() as uow:
        await replace_learned_identifiers(
            uow,
            {
                contact.id: [
                    Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example"),
                    Identifier(IdentifierKind.EMAIL_DOMAIN, "nord-ag.example"),
                ]
            },
            fetch_logos_at=NOW,
        )
        await uow.commit()
    assert await queued(world) == [
        {"contact_id": str(contact.id), "domain": "nord-ag.example", "asked": False}
    ]


async def test_an_uploaded_logo_is_never_replaced(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher({"nord.example": PNG})
    master, logos = world.fetching(fetcher)
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    uploaded = await logos.upload(admin.id, contact.id, image((90, 90), "JPEG"))
    assert await run_all(logos) == 1
    assert fetcher.calls == []  # nothing asked: the upload wins
    assert (await stored(world, contact.id)).logo == uploaded.logo
    with pytest.raises(ConflictError):
        await logos.request_fetch(admin.id, contact.id)
    assert await logos.fetch(contact.id, asked=True) is False


async def test_an_upload_during_a_fetch_wins(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)

    class Slow(FakeLogoFetcher):
        async def fetch(self, domain: str) -> FetchedLogo:
            found = await super().fetch(domain)
            await logos.upload(admin.id, contact.id, PNG)  # meanwhile
            return found

    master, logos = world.fetching(Slow({"nord.example": PNG}))
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    assert await logos.fetch(contact.id, domain="nord.example") is False
    logo = (await stored(world, contact.id)).logo
    assert logo is not None and logo.source is LogoSource.UPLOADED


async def test_an_admin_asks_again_for_each_domain(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    fetcher = FakeLogoFetcher({"zz-nord.example": PNG})
    master, logos = world.fetching(fetcher)
    other = Identifier(IdentifierKind.EMAIL_DOMAIN, "zz-nord.example")
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await run_all(logos)
    assert (await stored(world, contact.id)).logo_failure is not None
    await master.change_contact(admin.id, contact.id, identifiers=[DOMAIN, other])
    await run_all(logos)  # the new domain, on its own
    first = (await stored(world, contact.id)).logo
    assert first is not None and first.domain == "zz-nord.example"

    with pytest.raises(PermissionDeniedError):
        await logos.request_fetch(user.id, contact.id)
    world.clock.advance(timedelta(minutes=1))
    await logos.request_fetch(admin.id, contact.id)
    await logos.request_fetch(admin.id, contact.id)  # once while it waits
    assert await queued(world) == [{"contact_id": str(contact.id), "domain": None, "asked": True}]
    await run_all(logos)
    # Asked, it tries the failed domain again and goes on to the next; a new fetched logo.
    assert fetcher.calls[-2:] == ["nord.example", "zz-nord.example"]
    again = await stored(world, contact.id)
    assert again.logo is not None and again.logo.stored_at > first.stored_at

    bare = await master.create_contact(admin.id, "Bare")
    with pytest.raises(ValidationError):
        await logos.request_fetch(admin.id, bare.id)
    with pytest.raises(NotFoundError):
        await logos.request_fetch(admin.id, ContactId(new_id()))


async def test_fetch_logos_queues_the_contacts_without_one(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    fetcher = FakeLogoFetcher({"nord.example": PNG})
    master = world.master_data  # identifiers entered without fetching on
    with_domain = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await master.create_contact(admin.id, "Bare", identifiers=[IBAN])
    with_logo = await master.create_contact(
        admin.id, "Logo", identifiers=[Identifier(IdentifierKind.EMAIL_DOMAIN, "logo.example")]
    )
    await world.logos.upload(admin.id, with_logo.id, PNG)
    _, logos = world.fetching(fetcher)
    assert await logos.request_all() == 1
    assert await queued(world) == [
        {"contact_id": str(with_domain.id), "domain": "nord.example", "asked": False}
    ]
    assert await logos.request_all() == 1  # queued once while it waits
    assert len(await queued(world)) == 1
    await run_all(logos)
    assert fetcher.calls == ["nord.example"]


async def test_a_logo_that_breaks_the_normaliser_is_a_failed_fetch(world: World) -> None:
    """Whatever an image or an adapter breaks, the next domain is tried and the failure noted,
    so that `fetch-logos` does not queue the contact again and again."""
    admin = await world.user(role=Role.ADMIN)

    class Broken(FakeImageNormaliser):
        async def normalise(self, data: bytes) -> bytes:
            if data == b"broken":
                raise TypeError("bad operand type for abs(): 'str'")
            return await super().normalise(data)

    world.images = Broken()
    other = Identifier(IdentifierKind.EMAIL_DOMAIN, "zz-nord.example")
    master, logos = world.fetching(FakeLogoFetcher({"nord.example": b"broken"}))
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN, other])
    await logos.request_fetch(admin.id, contact.id)
    await run_all(logos)
    jobs = [job for job in world.database.jobs.values() if job.kind == LOGO_JOB]
    assert {job.status for job in jobs} == {JobStatus.DONE}
    failed = (await stored(world, contact.id)).logo_failure
    # Both were tried; neither is queued again by `fetch-logos`.
    assert failed is not None and failed.domains == {"nord.example", "zz-nord.example"}
    assert await logos.request_all() == 0


async def test_a_broken_job_is_tried_again_and_given_up(world: World) -> None:
    """What breaks beyond the website (the object store) is tried again, three times."""
    admin = await world.user(role=Role.ADMIN)

    class Down(MemoryObjectStore):
        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            raise OSError("store down")

    world.object_store = Down()
    master, logos = world.fetching(FakeLogoFetcher({"nord.example": PNG}))
    await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    for attempt in range(3):
        assert await logos.run_next_job()
        world.clock.advance(timedelta(minutes=5 * (attempt + 1)))
    assert await logos.run_next_job() is False
    statuses = [job.status for job in world.database.jobs.values() if job.kind == LOGO_JOB]
    assert statuses == [JobStatus.FAILED]


async def test_a_contact_gone_meanwhile_gets_no_logo(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    master, logos = world.fetching(FakeLogoFetcher({"nord.example": PNG}))
    contact = await master.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    await master.delete_contact(admin.id, contact.id)
    assert await run_all(logos) == 1
    assert not await world.object_store.exists(logo_key(contact.id))


class Meanwhile(MemoryObjectStore):
    """An object store during whose first `put` an admin renames the contact elsewhere."""

    def __init__(self, world: World, admin: UserId, contact: ContactId, name: str) -> None:
        super().__init__()
        self.world, self.admin, self.contact, self.name = world, admin, contact, name
        self.puts = 0

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.puts += 1
        if self.puts == 1:
            await self.world.master_data.change_contact(self.admin, self.contact, name=self.name)
        await super().put(key, data, content_type=content_type)


async def test_a_contact_changed_while_its_logo_is_stored_is_stored_again(world: World) -> None:
    """The row first, then the file; a change of the contact meanwhile (which takes no logo
    lock) fails the version check, and the step starts again on the new state, so that file
    and row tell the same logo."""
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Nord", identifiers=[DOMAIN])
    store = Meanwhile(world, admin.id, contact.id, "Nord AG")
    world.object_store = store
    uploaded = await world.logos.upload(admin.id, contact.id, PNG)
    assert store.puts == 2
    final = await stored(world, contact.id)
    assert (final.name, final.logo) == ("Nord AG", uploaded.logo)
    assert final.logo is not None and final.logo.source is LogoSource.UPLOADED
    assert await store.get(logo_key(contact.id)) == FAKE_LOGO

    # The same for a fetched logo.
    sued = Identifier(IdentifierKind.EMAIL_DOMAIN, "sued.example")
    other = await world.master_data.create_contact(admin.id, "Süd", identifiers=[sued])
    store = Meanwhile(world, admin.id, other.id, "Süd AG")
    world.object_store = store
    _, logos = world.fetching(FakeLogoFetcher({"sued.example": PNG}))
    assert await logos.fetch(other.id, domain="sued.example")
    fetched = await stored(world, other.id)
    assert fetched.name == "Süd AG" and store.puts == 2
    assert fetched.logo is not None and fetched.logo.domain == "sued.example"


async def test_the_row_is_written_before_the_file(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Nord")
    order: list[str] = []

    class Recording(MemoryObjectStore):
        async def put(self, key: str, data: bytes, *, content_type: str) -> None:
            order.append("put")
            await super().put(key, data, content_type=content_type)

    world.object_store = Recording()
    factory = world.uow

    def recording() -> MemoryUnitOfWork:
        uow = factory()
        update = uow.contacts.update

        async def recorded(item: Contact) -> None:
            order.append("update")
            await update(item)

        uow.contacts.update = recorded  # type: ignore[method-assign, assignment]
        return uow

    logos = LogoService(recording, world.clock, world.object_store, world.images)
    await logos.upload(admin.id, contact.id, PNG)
    assert order == ["update", "put"]

"""Logos of contacts: uploaded by admins, fetched from their websites, read by every signed-in
user (master data is global).

The logo is stored as a small WebP at `objects.logo_key`; the contact names where it came from
and when (`Contact.logo`). Storing and removing the file and the contact's change happen under
one lock per contact (`logo_lock`), so that the file and the contact always tell the same
logo, also when an upload, a removal, a deletion of the contact and a fetch meet. The row is
written before the file: another change of the contact meanwhile (it takes no logo lock) fails
the row's version check there, on SQL before the file is touched (and holds the row until the
commit), and the step starts again on the new state (`_retrying`).

Fetching (with a `LogoFetcher`, `PAPIQ_LOGO_FETCH`) runs as the job `contacts.logo`, queued
when a contact without a logo, or with a fetched one, gets a domain among its identifiers
(entered by hand, learned by training or attached from a document: `queue_logo_fetch`), when an
admin asks (`request_fetch`) and for every contact without one (`request_all`, the command
`fetch-logos`). An uploaded logo is never replaced. The domains whose fetch found nothing are
noted (`Contact.logo_failure`); they are tried again only when an admin asks. A job that runs
while fetching is off ends at once, with a note.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from papiq.core.domain import media_types
from papiq.core.domain.appearance import ContactLogo, LogoSource
from papiq.core.domain.errors import (
    ConcurrencyError,
    ConflictError,
    ImageError,
    LogoFetchDisabledError,
    LogoFetchError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from papiq.core.domain.ids import ContactId, UserId
from papiq.core.domain.jobs import Job
from papiq.core.domain.master_data import Contact
from papiq.core.domain.permissions import can_manage_master_data
from papiq.core.ports import (
    Clock,
    ImageNormaliser,
    LogoFetcher,
    ObjectStore,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor
from papiq.core.services.objects import logo_key

log = logging.getLogger(__name__)

LOGO_MAX_SIZE = 1024 * 1024
"""Bytes of an uploaded image."""
LOGO_TYPE = "image/webp"
LOGO_JOB = "contacts.logo"
FETCHING_OFF = "not fetched: fetching logos is off (PAPIQ_LOGO_FETCH)"
"""The note of a job that ran while fetching was off."""
MAX_DOMAINS = 3
"""Domains of a contact an admin's request tries, in order, until one has a logo."""
# A job asks at most `MAX_DOMAINS` websites, each within 30 s, and makes a logo of what it
# found, which the normaliser bounds to seconds (pixels, scans, pictures): well within this.
_LEASE = timedelta(minutes=10)
_ATTEMPTS = 3  # tries of a job that broke for another reason than the website
_RETRY_DELAY = timedelta(minutes=5)
_COMMIT_ATTEMPTS = 3  # the contact changed while its logo was stored: again


def logo_lock(contact: ContactId) -> str:
    """The lock (`UnitOfWork.lock`) a unit takes before it stores or removes the logo of a
    contact, or deletes the contact."""
    return f"contact-logo:{contact}"


@dataclass(frozen=True)
class StoredLogo:
    data: bytes  # WebP
    logo: ContactLogo


class LogoService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        store: ObjectStore,
        images: ImageNormaliser,
        fetcher: LogoFetcher | None = None,
    ) -> None:
        """`fetcher`: with one, logos are fetched from the websites of contacts."""
        self._uow = uow
        self._clock = clock
        self._store = store
        self._images = images
        self._fetcher = fetcher

    @property
    def fetching(self) -> bool:
        """Whether logos are fetched from websites (`PAPIQ_LOGO_FETCH`)."""
        return self._fetcher is not None

    async def upload(self, actor: UserId, id: ContactId, data: bytes) -> Contact:
        """Admins only. `data`, a PNG, JPEG or WebP image of at most `LOGO_MAX_SIZE` bytes,
        becomes the contact's logo, in place of any other; a fetched one never replaces it.
        UnsupportedMediaTypeError for another kind (SVG: it can carry scripts),
        ValidationError if it is too large, ImageError if it cannot be read."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            await uow.contacts.get(id)
        if len(data) > LOGO_MAX_SIZE:
            raise ValidationError(f"a logo is at most {LOGO_MAX_SIZE} bytes")
        if media_types.detect_image(data[: media_types.SNIFF_SIZE]) not in media_types.LOGO_UPLOADS:
            raise UnsupportedMediaTypeError("a logo is a PNG, JPEG or WebP image")
        image = await self._images.normalise(data)

        async def store() -> Contact:
            async with self._uow() as uow:
                await uow.lock(logo_lock(id))
                await _require_admin(uow, actor)
                contact = await uow.contacts.get(id)
                logo = ContactLogo(source=LogoSource.UPLOADED, stored_at=self._clock.now())
                contact.set_logo(logo)
                await uow.contacts.update(contact)
                await self._store.put(logo_key(id), image, content_type=LOGO_TYPE)
                await uow.commit()
            return contact

        return await _retrying(store)

    async def remove(self, actor: UserId, id: ContactId) -> Contact:
        """Admins only: the contact shows its initials again; a failed fetch noted is
        forgotten."""

        async def drop() -> Contact:
            async with self._uow() as uow:
                await uow.lock(logo_lock(id))
                await _require_admin(uow, actor)
                contact = await uow.contacts.get(id)
                if contact.logo is None and contact.logo_failure is None:
                    return contact
                contact.remove_logo()
                await uow.contacts.update(contact)
                await self._store.delete(logo_key(id))
                await uow.commit()
            return contact

        return await _retrying(drop)

    async def logo(self, actor: UserId, id: ContactId) -> StoredLogo:
        """Every signed-in user. NotFoundError if the contact has no logo."""
        async with self._uow() as uow:
            await load_actor(uow, actor)
            contact = await uow.contacts.get(id)
        if contact.logo is None:
            raise NotFoundError("logo of contact", id)
        return StoredLogo(await self._store.get(logo_key(id)), contact.logo)

    # --- fetching ---------------------------------------------------------------------------

    async def request_fetch(self, actor: UserId, id: ContactId) -> None:
        """Admins only: fetch the contact's logo from its website now, trying its domains in
        turn, also one whose fetch failed before; a logo found replaces a fetched one.
        LogoFetchDisabledError without fetching; ConflictError while it has an uploaded logo;
        ValidationError without a domain among its identifiers."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            if self._fetcher is None:
                raise LogoFetchDisabledError("Papiq does not fetch logos (PAPIQ_LOGO_FETCH is off)")
            contact = await uow.contacts.get(id)
            if contact.logo is not None and contact.logo.source is LogoSource.UPLOADED:
                raise ConflictError(
                    f"'{contact.name}' has an uploaded logo; a fetched one never replaces it"
                )
            if not contact.domains:
                raise ValidationError(f"'{contact.name}' has no domain among its identifiers")
            await _enqueue(uow, contact.id, None, self._clock.now())
            await uow.commit()

    async def request_all(self) -> int:
        """`fetch-logos`: queue a fetch for every contact without a logo that has a domain
        whose fetch has not failed. Returns how many. LogoFetchDisabledError without
        fetching."""
        if self._fetcher is None:
            raise LogoFetchDisabledError("Papiq does not fetch logos (PAPIQ_LOGO_FETCH is off)")
        queued = 0
        async with self._uow() as uow:
            for contact in await uow.contacts.list_all():
                if contact.logo is None:
                    queued += await queue_logo_fetch(uow, contact, (), self._clock.now())
            await uow.commit()
        return queued

    async def run_next_job(self) -> bool:
        """Claim and run a due fetch; False if none was due. Without fetching (switched off
        since the job was queued) the job ends at once with a note, neither waiting nor
        tried again."""
        async with self._uow() as uow:
            job = await uow.jobs.claim(now=self._clock.now(), lease=_LEASE, kinds=[LOGO_JOB])
            await uow.commit()
        if job is None:
            return False
        if self._fetcher is None:
            await self._end(job)
            return True
        try:
            await self._run(job)
        except asyncio.CancelledError:
            await asyncio.shield(self._release(job))
            raise
        return True

    async def fetch(self, id: ContactId, *, domain: str | None = None, asked: bool = False) -> bool:
        """Fetch the contact's logo and store it; whether one was stored. Queued for a new
        `domain`: only that one, while it is the contact's, has no failed fetch and is not
        where its fetched logo came from; a logo found replaces a fetched one, none found
        leaves it. `asked` (an admin's request, no domain): the contact's domains in turn (at
        most `MAX_DOMAINS`), also one that failed, replacing a fetched logo. An uploaded logo
        is never replaced; a fetch that finds nothing notes the domains it tried."""
        assert self._fetcher is not None
        async with self._uow() as uow:
            contact = await uow.contacts.find(id)
        if contact is None or _uploaded(contact):
            return False
        if asked:
            domains = contact.domains[:MAX_DOMAINS]
        elif domain not in contact.domains or not _new(contact, domain):
            domains = []
        else:
            domains = [domain]
        if not domains:
            return False
        found = await self._found(domains)

        async def store() -> bool:
            async with self._uow() as uow:
                await uow.lock(logo_lock(id))
                contact = await uow.contacts.find(id)
                if contact is None or _uploaded(contact):
                    return False
                if not asked and domain is not None and not _new(contact, domain):
                    return False  # fetched from it meanwhile
                now = self._clock.now()
                if found is None:
                    contact.note_failed_fetch(domains, now)
                else:
                    contact.set_logo(
                        ContactLogo(source=LogoSource.FETCHED, stored_at=now, domain=found[0])
                    )
                await uow.contacts.update(contact)
                if found is not None:
                    await self._store.put(logo_key(id), found[1], content_type=LOGO_TYPE)
                await uow.commit()
            return found is not None

        return await _retrying(store)

    async def _found(self, domains: Collection[str]) -> tuple[str, bytes] | None:
        """The first logo the websites at `domains` have, made a logo, with its domain."""
        assert self._fetcher is not None
        for domain in domains:
            try:
                fetched = await self._fetcher.fetch(domain)
                image = await self._images.normalise(fetched.data)
            except (LogoFetchError, ImageError, UnsupportedMediaTypeError) as error:
                log.info("no logo fetched", extra={"domain": domain, "error": str(error)})
                continue
            except Exception:  # a broken adapter: no logo from this website either
                log.warning("no logo fetched", extra={"domain": domain}, exc_info=True)
                continue
            log.info("logo fetched", extra={"domain": domain, "url": fetched.url})
            return domain, image
        return None

    async def _run(self, job: Job) -> None:
        payload = job.payload
        try:
            domain = payload.get("domain")
            await self.fetch(
                ContactId(UUID(str(payload["contact_id"]))),
                domain=domain if isinstance(domain, str) else None,
                asked=bool(payload.get("asked")),
            )
        except Exception as error:
            await self._failed(job, error)
            return
        try:
            async with self._uow() as uow:
                await uow.jobs.complete(job)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a logo job", extra={"job_id": str(job.id)})

    async def _failed(self, job: Job, error: Exception) -> None:
        reason = f"{type(error).__name__}: {error}"
        log.warning("logo job failed", exc_info=True)
        try:
            async with self._uow() as uow:
                if job.tries >= _ATTEMPTS:
                    await uow.jobs.fail(job, error=reason)
                else:
                    run_at = self._clock.now() + _RETRY_DELAY * job.tries
                    await uow.jobs.reschedule(job, run_at=run_at, error=reason)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a logo job", extra={"job_id": str(job.id)})

    async def _release(self, job: Job) -> None:
        try:
            async with self._uow() as uow:
                await uow.jobs.release(job, run_at=self._clock.now(), error="worker stopped")
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a logo job", extra={"job_id": str(job.id)})

    async def _end(self, job: Job) -> None:
        """A fetch queued while fetching was on: given up at once, with a note."""
        log.info("logo not fetched: fetching is off", extra={"job_id": str(job.id)})
        try:
            async with self._uow() as uow:
                await uow.jobs.fail(job, error=FETCHING_OFF)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a logo job", extra={"job_id": str(job.id)})


async def _retrying[T](step: Callable[[], Awaitable[T]]) -> T:
    """`step` again while the contact changed meanwhile, at most `_COMMIT_ATTEMPTS` times."""
    for attempt in range(1, _COMMIT_ATTEMPTS + 1):
        try:
            return await step()
        except ConcurrencyError:
            if attempt == _COMMIT_ATTEMPTS:
                raise
            log.info("a contact changed while its logo was stored; storing again")
    raise AssertionError("unreachable")


async def queue_logo_fetch(
    uow: UnitOfWork, contact: Contact, known: Collection[str], now: datetime
) -> bool:
    """Queue fetching the logo of `contact`, in `uow`, if it has no logo or a fetched one and a
    domain beyond `known` (those it had before the change) whose fetch has not failed and that
    the fetched logo did not come from: the first of them. An uploaded logo is never replaced.
    Callers ask only with fetching on. Whether a fetch was queued."""
    if _uploaded(contact):
        return False
    for domain in contact.domains:
        if domain not in known and _new(contact, domain):
            await _enqueue(uow, contact.id, domain, now)
            return True
    return False


async def _enqueue(uow: UnitOfWork, contact: ContactId, domain: str | None, now: datetime) -> None:
    """A fetch for `domain`, or one an admin asked for (None); each once while it waits."""
    await uow.jobs.enqueue(
        LOGO_JOB,
        {"contact_id": str(contact), "domain": domain, "asked": domain is None},
        run_at=now,
        dedup_key=f"{LOGO_JOB}:{contact}:{domain or 'asked'}",
    )


def _uploaded(contact: Contact) -> bool:
    return contact.logo is not None and contact.logo.source is LogoSource.UPLOADED


def _new(contact: Contact, domain: str) -> bool:
    """Whether a fetch from `domain` may find the contact a logo: it did not fail before, and
    the logo the contact has did not come from it."""
    came_from = None if contact.logo is None else contact.logo.domain
    return not contact.fetch_failed(domain) and domain != came_from


async def drop_logo(uow: UnitOfWork, store: ObjectStore, contact: ContactId) -> None:
    """Before a contact is deleted, in `uow`: its logo goes with it. Takes the contact's logo
    lock (the unit's only one), so that no logo is stored for it meanwhile."""
    await uow.lock(logo_lock(contact))
    await store.delete(logo_key(contact))


async def _require_admin(uow: UnitOfWork, actor: UserId) -> None:
    if not can_manage_master_data(await load_actor(uow, actor)):
        raise PermissionDeniedError("only admins change master data")

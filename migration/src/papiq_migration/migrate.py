"""The migration: users, master data, drawers, then documents. The same code path serves the
trial run (`dry`: reads only, writes nothing, knows nothing of Papiq but what it can read) and
the real run (resumable: what the state file knows is not done twice)."""

import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from papiq_migration.config import Config
from papiq_migration.mapping import (
    ASN_SPEC,
    NAME_LIMIT,
    NOTES_SPEC,
    AttributeSpec,
    drawer_name,
    field_key,
    field_spec,
    norm,
)
from papiq_migration.paperless import Paperless, PaperlessError, Snapshot
from papiq_migration.papiq import ApiError, Papiq
from papiq_migration.prepare import Ids, Prepared, prepare
from papiq_migration.state import DOCUMENT_DONE, State

NEW, EXISTING, OMITTED, FAILED = "new", "existing", "omitted", "failed"
DONE_PIPELINE = ("completed", "failed", "review")
NAMED = (
    ("contact", "/contacts", "correspondents", "contacts"),
    ("document_type", "/document-types", "document_types", "document_types"),
    ("tag", "/tags", "tags", "tags"),
)

Progress = Callable[[str], None]


@dataclass
class Totals:
    """What happened to the documents in this run."""

    documents: int = 0
    uploaded: int = 0
    resumed: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: int = 0
    seconds: dict[str, float] = field(default_factory=dict)


class MigrationError(Exception):
    """The migration cannot go on (a wrong token, an unreachable server)."""


class Migration:
    def __init__(
        self,
        config: Config,
        source: Paperless,
        target: Papiq | None,
        state: State,
        *,
        dry: bool,
        stop: asyncio.Event | None = None,
        progress: Progress = lambda message: None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll: tuple[float, float] = (0.5, 5.0),
    ) -> None:
        if target is None and not dry:
            raise MigrationError("a real run needs the Papiq URL and API token")
        self._config = config
        self._source = source
        self._target = target
        self._state = state
        self._dry = dry
        self._stop = stop or asyncio.Event()
        self._progress = progress
        self._clock = clock
        self._sleep = sleep
        self._poll = poll
        self.totals = Totals()
        self.snapshot: Snapshot | None = None
        self.ids = Ids(admin="admin")
        self.prepared: list[Prepared] = []
        self._inactive: list[tuple[str, str]] = []
        self._asleep: dict[str, str] = {}  # ours, inactive in Papiq from an earlier run

    # --- the whole run -----------------------------------------------------------------------

    async def execute(self) -> Totals:
        started = self._clock()
        try:
            self.snapshot = snapshot = await self._source.snapshot(limit=self._config.limit)
        except PaperlessError as error:
            raise MigrationError(str(error)) from None
        self._progress(
            f"Paperless {snapshot.version}: {len(snapshot.documents)} documents, "
            f"{len(snapshot.users)} users"
        )
        self.totals.seconds["read"] = self._clock() - started
        await self._admin()
        phase = self._clock()
        await self._users(snapshot)
        await self._master_data(snapshot)
        await self._attributes(snapshot)
        self.totals.seconds["master_data"] = self._clock() - phase
        self._note_unmapped(snapshot)
        users = {int(user["id"]): user for user in snapshot.users}
        fields = {int(item["id"]): item for item in snapshot.custom_fields}
        self.prepared = [
            prepare(
                document,
                ids=self.ids,
                users=users,
                custom_fields=fields,
                currency=self._config.currency,
            )
            for document in snapshot.documents
        ]
        await self._wake()
        phase = self._clock()
        drawers = await self._drawers()
        self.totals.seconds["drawers"] = self._clock() - phase
        phase = self._clock()
        await self._documents(drawers)
        self.totals.seconds["documents"] = self._clock() - phase
        if not self._stop.is_set():
            await self._deactivate()
        self.totals.seconds["total"] = self._clock() - started
        return self.totals

    # --- reading Papiq -----------------------------------------------------------------------

    async def _list(self, path: str) -> list[dict[str, Any]]:
        return [] if self._target is None else await self._target.items(path)

    async def _admin(self) -> None:
        if self._target is None:
            return
        try:
            me = await self._target.me()
        except ApiError as error:
            raise MigrationError(f"Papiq refused the API key: {error}") from None
        user = me["user"]
        if user.get("role") != "admin":
            raise MigrationError("the Papiq API key must belong to an admin")
        if not self._dry and me.get("token_scope") != "read_write":
            raise MigrationError("the Papiq API key needs the scope read_write")
        self.ids.admin = user["id"]
        self.ids.usernames[norm(user["username"])] = user["id"]

    def _record(
        self,
        kind: str,
        source_id: str,
        name: str,
        papiq_id: str | None,
        status: str,
        notes: list[str],
    ) -> None:
        """Remember an object. A second run finds what the first created: it stays `new`."""
        prior = self._state.object(kind, source_id)
        if prior is not None and prior[0] == papiq_id and prior[1] == NEW and status == EXISTING:
            status = NEW
        self._state.put_object(kind, source_id, name, papiq_id, status, notes)

    # --- users -------------------------------------------------------------------------------

    async def _users(self, snapshot: Snapshot) -> None:
        """Users by name; missing ones are created (active: a user who owns documents or gets
        shares must be active; the ones inactive in Paperless are deactivated at the end)."""
        existing = {norm(user["username"]): user for user in await self._list("/users")}
        for user in snapshot.users:
            name = str(user["username"])
            notes: list[str] = []
            found = existing.get(norm(name))
            if user.get("is_superuser") and found is None:
                notes.append(
                    "administrator in Paperless: role 'user' in Papiq (admins are named by hand)"
                )
            active = bool(user.get("is_active", True))
            ours = (self._state.object("user", str(user["id"])) or (None, ""))[1] == NEW
            try:
                if found is None and not (self._dry or self._target is None):
                    try:
                        found = await self._target.create_user(name)
                        ours = True
                    except ApiError as error:
                        if error.status != 409:
                            raise
                        # Created by an earlier attempt whose answer was lost.
                        found = next(
                            (
                                u
                                for u in await self._list("/users")
                                if norm(u["username"]) == norm(name)
                            ),
                            None,
                        )
                        if found is None:
                            raise
                    existing[norm(name)] = found
                    status = NEW if ours else EXISTING
                    id = found["id"]
                elif found is not None:
                    id, status = found["id"], NEW if ours else EXISTING
                else:
                    id, status = f"new:{name}", NEW
                if not active:
                    if status == NEW:
                        notes.append("deactivated at the end of the migration")
                        if found is not None and found.get("active") is False:
                            self._asleep[id] = name
                        else:
                            self._inactive.append((id, name))
                    else:
                        notes.append("inactive in Paperless; the Papiq user stays as it is")
                if found is not None and found.get("active") is False and status == EXISTING:
                    self.ids.inactive.add(id)
            except ApiError as error:
                self._record("user", str(user["id"]), name, None, FAILED, [str(error)])
                raise MigrationError(f"cannot create the user '{name}': {error}") from None
            self.ids.users[int(user["id"])] = id
            self.ids.usernames[norm(name)] = id
            self._record("user", str(user["id"]), name, id, status, notes)
        for group in snapshot.groups:
            self._record(
                "group",
                str(group["id"]),
                str(group["name"]),
                None,
                OMITTED,
                ["only used to resolve document permissions to users"],
            )

    async def _wake(self) -> None:
        """Activate the users deactivated by an earlier run if documents of theirs (owned or
        shared) are still to be taken over; they are deactivated again at the end."""
        if self._dry or self._target is None or not self._asleep:
            return
        needed: set[str] = set()
        for item in self.prepared:
            row = self._state.document(item.id)
            if item.skip is None and not (row is not None and row.status in DOCUMENT_DONE):
                needed.add(item.owner)
                needed.update(self.ids.usernames[norm(name)] for name, _ in item.shares)
        for id, name in self._asleep.items():
            if id in needed:
                await self._target.set_active(id, True)
                self._inactive.append((id, name))

    async def _deactivate(self) -> None:
        """The users that are inactive in Paperless are inactive in Papiq, too, now that no
        document of theirs is left to upload."""
        if self._dry or self._target is None:
            return
        for id, name in self._inactive:
            try:
                await self._target.set_active(id, False)
            except ApiError as error:
                self._progress(f"cannot deactivate {name}: {error}")

    # --- contacts, document types, tags ------------------------------------------------------

    async def _master_data(self, snapshot: Snapshot) -> None:
        for kind, path, source_key, ids_key in NAMED:
            existing = {norm(item["name"]): item for item in await self._list(path)}
            mapping: dict[int, str] = getattr(self.ids, ids_key)
            first: dict[str, str] = {}
            for item in getattr(snapshot, source_key):
                name = str(item["name"]).strip()
                notes = _named_notes(kind, item)
                if len(name) > NAME_LIMIT:
                    name = name[:NAME_LIMIT].rstrip()
                    notes.append(f"the name was shortened to {NAME_LIMIT} characters")
                key = norm(name)
                found = existing.get(key)
                try:
                    if found is not None:
                        id, status = found["id"], EXISTING
                        if key in first:
                            notes.append(f"same name as '{first[key]}': merged")
                    elif self._dry or self._target is None:
                        id, status = f"new:{kind}:{key}", NEW
                    else:
                        try:
                            id, status = (await self._target.create_named(path, name))["id"], NEW
                        except ApiError as error:
                            if error.status != 409:
                                raise
                            # Created by an earlier attempt whose answer was lost.
                            again = {norm(i["name"]): i for i in await self._list(path)}
                            if key not in again:
                                raise
                            id, status = again[key]["id"], EXISTING
                except ApiError as error:
                    self._record(kind, str(item["id"]), name, None, FAILED, [str(error)])
                    raise MigrationError(f"cannot create {kind} '{name}': {error}") from None
                if found is None:
                    existing[key] = {"id": id, "name": name}
                first.setdefault(key, name)
                mapping[int(item["id"])] = id
                self._record(kind, str(item["id"]), name, id, status, notes)

    # --- attributes --------------------------------------------------------------------------

    async def _attributes(self, snapshot: Snapshot) -> None:
        existing = {norm(item["name"]): item for item in await self._list("/attributes")}
        specs: list[tuple[str, str, AttributeSpec | None, list[str]]] = []
        for custom_field in snapshot.custom_fields:
            spec, notes = field_spec(custom_field)
            specs.append((str(custom_field["id"]), str(custom_field["name"]), spec, notes))
        if any(
            document.get("archive_serial_number") is not None for document in snapshot.documents
        ):
            specs.append(("asn", ASN_SPEC.name, ASN_SPEC, ["the archive serial number"]))
        if any(document.get("notes") for document in snapshot.documents):
            specs.append(("notes", NOTES_SPEC.name, NOTES_SPEC, ["the notes of the documents"]))
        for source_id, name, spec, notes in specs:
            if spec is None:
                self._record("attribute", source_id, name, None, OMITTED, notes)
                continue
            found = existing.get(norm(spec.name))
            status, id = NEW, None
            try:
                if found is not None:
                    status, id = EXISTING, found["id"]
                    problem = _conflict(spec, found)
                    if problem:
                        self._record("attribute", source_id, name, None, OMITTED, [*notes, problem])
                        continue
                    missing = [c for c in spec.choices if c not in found.get("choices", [])]
                    if missing:
                        notes.append(f"the options {missing} are added")
                        if not self._dry and self._target is not None:
                            await self._target.set_choices(id, [*found["choices"], *missing])
                elif self._dry or self._target is None:
                    id = f"new:attribute:{spec.key}"
                else:
                    created = await self._target.create_attribute(
                        spec.name, spec.data_type, spec.choices
                    )
                    id = created["id"]
            except ApiError as error:
                self._record("attribute", source_id, name, None, FAILED, [*notes, str(error)])
                continue
            assert id is not None
            self.ids.attributes[spec.key] = id
            self.ids.specs[spec.key] = spec
            self._record("attribute", source_id, name, id, status, notes)

    def _note_unmapped(self, snapshot: Snapshot) -> None:
        counts: dict[str, int] = {}
        for document in snapshot.documents:
            if document.get("storage_path") is not None:
                counts[str(document["storage_path"])] = (
                    counts.get(str(document["storage_path"]), 0) + 1
                )
        for path in snapshot.storage_paths:
            self._record(
                "storage_path",
                str(path["id"]),
                str(path["name"]),
                None,
                OMITTED,
                [f"no counterpart in Papiq; {counts.get(str(path['id']), 0)} documents use it"],
            )
        for name, count in snapshot.counted.items():
            what = "could not be read" if count < 0 else f"{count} not taken over"
            self._record("other", name, name.replace("_", " "), None, OMITTED, [what])
        for name, text in (("date_added", "date added"), ("history", "history")):
            self._record(
                "other",
                name,
                text,
                None,
                OMITTED,
                [f"not taken over for the {len(snapshot.documents)} documents"],
            )
        # Make sure the ids of attributes that could not be created do not linger.
        for key in list(self.ids.specs):
            if key not in self.ids.attributes:
                del self.ids.specs[key]

    # --- drawers -----------------------------------------------------------------------------

    async def _drawers(self) -> dict[str, str]:
        """The drawers the documents need: one per owner and combination of readers and
        writers, shared accordingly. Returns the Papiq id by key."""
        wanted: dict[str, Prepared] = {}
        for item in self.prepared:
            key = item.drawer_key()
            if item.skip is None and key is not None:
                wanted.setdefault(key, item)
        existing: dict[tuple[str, str], dict[str, Any]] = {}
        for drawer in await self._list("/drawers"):
            existing[(drawer["owner_id"], norm(drawer["name"]))] = drawer
        result: dict[str, str] = {}
        for key, item in sorted(wanted.items()):
            name = drawer_name(item.shares)
            found = existing.get((item.owner, norm(name)))
            notes = [f"owner {item.owner_name or 'the executing admin'}"]
            status = EXISTING
            try:
                if found is not None:
                    id = found["id"]
                elif self._dry or self._target is None:
                    id, status = f"new:drawer:{key}", NEW
                else:
                    id, status = (await self._target.create_drawer(name, item.owner))["id"], NEW
                    found = {"shares": []}
                have = {
                    share["user_id"]: share["level"] for share in (found or {}).get("shares") or []
                }
                for username, level in item.shares:
                    user_id = self.ids.usernames[norm(username)]
                    if have.get(user_id) != level and not self._dry and self._target is not None:
                        await self._target.share(id, user_id, level)
            except ApiError as error:
                # The documents that need this drawer fail with this reason; the others go on.
                self._record("drawer", key, name, None, FAILED, [str(error)])
                continue
            result[key] = id
            self._record("drawer", key, name, id, status, notes)
        return result

    # --- documents ---------------------------------------------------------------------------

    async def _documents(self, drawers: dict[str, str]) -> None:
        self.totals.documents = len(self.prepared)
        done = 0
        gate = asyncio.Semaphore(self._config.concurrency)

        async def one(item: Prepared) -> None:
            nonlocal done
            async with gate:
                if self._stop.is_set():
                    self.totals.stopped += 1
                    return
                try:
                    await self._document(item, drawers)
                except Exception as error:  # one document never stops the others
                    self.totals.failed += 1
                    row = self._state.document(item.id)
                    # An upload stays `uploaded`: the next run waits for it, it does not repeat it.
                    self._state.put_document(
                        item.id,
                        "uploaded" if row is not None and row.status == "uploaded" else FAILED,
                        reason=f"{type(error).__name__}: {error}"[:300],
                    )
                done += 1
                if done % 25 == 0 or done == len(self.prepared):
                    self._progress(f"{done}/{len(self.prepared)} documents handled")

        await asyncio.gather(*(one(item) for item in self.prepared))

    def _detail(self, item: Prepared) -> dict[str, Any]:
        key = item.drawer_key()
        return {
            "title": item.title,
            "owner": item.owner_name,
            "drawer": drawer_name(item.shares) if key else "default",
            "media_type": item.media_type,
        }

    async def _document(self, item: Prepared, drawers: dict[str, str]) -> None:
        detail = self._detail(item)
        prior = self._state.document(item.id)
        if self._dry:
            if item.skip:
                self._state.put_document(
                    item.id, "skipped", reason=item.skip, notes=item.notes, detail=detail
                )
                self.totals.skipped += 1
            else:
                self._state.put_document(item.id, "planned", notes=item.notes, detail=detail)
            return
        if item.skip:
            self._state.put_document(
                item.id, "skipped", reason=item.skip, notes=item.notes, detail=detail
            )
            self.totals.skipped += 1
            return
        if prior is not None and prior.status in DOCUMENT_DONE:
            self.totals.resumed += 1
            return
        assert self._target is not None
        papiq_id = prior.papiq_id if prior is not None and prior.status == "uploaded" else None
        upload_seconds = prior.upload_seconds if prior else None
        sha256 = prior.sha256 if prior else None
        if papiq_id is not None:
            try:
                await self._target.document(papiq_id)
            except ApiError as error:
                if error.status != 404:
                    raise
                papiq_id = None
            else:
                self.totals.resumed += 1
        if papiq_id is None:
            began = self._clock()
            uploaded = await self._upload(item, drawers, detail)
            if uploaded is None:
                return
            papiq_id, sha256, duplicate = uploaded
            upload_seconds = self._clock() - began
            detail = {**detail, "duplicate": duplicate}
            self._state.put_document(
                item.id,
                "uploaded",
                papiq_id=papiq_id,
                sha256=sha256,
                notes=item.notes,
                detail=detail,
                upload_seconds=upload_seconds,
            )
            if not duplicate:
                self.totals.uploaded += 1
        waited = self._clock()
        finished = await self._wait(papiq_id)
        if finished is None:
            if not self._stop.is_set():
                self.totals.failed += 1
                # Stays `uploaded`: a later run waits for it again instead of uploading again.
                self._state.put_document(
                    item.id,
                    "uploaded",
                    papiq_id=papiq_id,
                    reason=f"processing did not finish in {int(self._config.pipeline_timeout)} s",
                )
            else:
                self.totals.stopped += 1
            return
        lane = finished.get("lane")
        reason = None
        if lane != "green":
            reason = await self._why_not_green(papiq_id)
        known = self._state.document(item.id)
        duplicate = bool(known is not None and known.detail.get("duplicate"))
        if duplicate:
            reason = "; ".join(filter(None, ["Papiq has the file already (same owner)", reason]))
        self._state.put_document(
            item.id,
            "duplicate" if duplicate else "done",
            papiq_id=papiq_id,
            lane=lane,
            reason=reason,
            pipeline_seconds=self._clock() - waited,
        )

    async def _upload(
        self, item: Prepared, drawers: dict[str, str], detail: dict[str, Any]
    ) -> tuple[str, str, bool] | None:
        """Download the original and upload it with owner, drawer and metadata; the Papiq id,
        the file's SHA-256 and whether Papiq had the file already. None: not taken over
        (recorded)."""
        assert self._target is not None
        key = item.drawer_key()
        fields = {
            "channel": "migration",
            "owner": item.owner,
            "metadata": json.dumps(item.metadata),
        }
        if key is not None:
            if key not in drawers:
                self.totals.failed += 1
                self._state.put_document(
                    item.id,
                    FAILED,
                    reason="its drawer could not be prepared (see the drawers)",
                    notes=item.notes,
                    detail=detail,
                )
                return None
            fields["drawer_id"] = drawers[key]
        with tempfile.TemporaryDirectory(prefix="papiq-migration-") as directory:
            path = Path(directory) / "original"
            try:
                download = await self._source.download_original(item.id, path)
            except PaperlessError as error:
                self.totals.failed += 1
                self._state.put_document(
                    item.id, FAILED, reason=str(error), notes=item.notes, detail=detail
                )
                return None
            try:
                accepted = await self._target.upload(path, item.filename, fields)
            except ApiError as error:
                if error.status == 409 and error.existing:
                    self.totals.duplicates += 1
                    return error.existing, download.sha256, True
                self._refused(item, error, download.sha256, detail)
                return None
        return accepted["id"], download.sha256, False

    def _refused(
        self, item: Prepared, error: ApiError, sha256: str, detail: dict[str, Any]
    ) -> None:
        if error.status == 415:
            self.totals.skipped += 1
            self._state.put_document(
                item.id,
                "skipped",
                sha256=sha256,
                reason=error.detail,
                notes=item.notes,
                detail=detail,
            )
        else:
            self.totals.failed += 1
            self._state.put_document(
                item.id, FAILED, sha256=sha256, reason=str(error), notes=item.notes, detail=detail
            )

    async def _wait(self, papiq_id: str) -> dict[str, Any] | None:
        """The document once its pipeline is through (or has stopped); None if the run is
        stopped or the time is up."""
        assert self._target is not None
        deadline = self._clock() + self._config.pipeline_timeout
        delay = self._poll[0]
        while not self._stop.is_set():
            document = await self._target.document(papiq_id)
            if document["processing"]["status"] in DONE_PIPELINE:
                return document
            if self._clock() > deadline:
                return None
            await self._sleep(delay)
            delay = min(delay * 1.5, self._poll[1])
        return None

    async def _why_not_green(self, papiq_id: str) -> str:
        assert self._target is not None
        try:
            log = await self._target.log(papiq_id)
        except ApiError:
            return "not green (the log could not be read)"
        reasons = [
            f"{entry['step']}: {entry['reason']}"
            for entry in log
            if entry["outcome"] != "ok" and entry.get("reason")
        ]
        return "; ".join(dict.fromkeys(reasons)) or "not green"


def _named_notes(kind: str, item: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if item.get("matching_algorithm") not in (None, 0):
        notes.append("the matching rule is not taken over")
    if kind == "tag":
        if item.get("parent"):
            notes.append(f"the hierarchy (parent {item['parent']}) is not taken over")
        if item.get("is_inbox_tag"):
            notes.append("the inbox flag is not taken over")
    if item.get("owner") is not None:
        notes.append("the owner of the Paperless object is not taken over (master data is global)")
    return notes


def _conflict(spec: AttributeSpec, found: dict[str, Any]) -> str | None:
    if found.get("data_type") != spec.data_type:
        return (
            f"an attribute '{found['name']}' of type {found.get('data_type')} exists already; "
            f"the values (type {spec.data_type}) are not taken over"
        )
    if found.get("document_type_ids") is not None:
        return f"the attribute '{found['name']}' exists but applies only to some document types"
    return None


__all__ = [
    "EXISTING",
    "FAILED",
    "NEW",
    "OMITTED",
    "Migration",
    "MigrationError",
    "Totals",
    "field_key",
]

"""The check: Paperless against Papiq, object by object. Every difference is reported; what
Papiq does not take over by decision is counted, not reported as a difference."""

import asyncio
import re
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from papiq_migration.config import Config, public
from papiq_migration.mapping import (
    ASN_SPEC,
    NOTES_SPEC,
    drawer_name,
    field_key,
    field_spec,
    norm,
)
from papiq_migration.migrate import Progress
from papiq_migration.paperless import Paperless
from papiq_migration.papiq import Papiq
from papiq_migration.prepare import Ids, Prepared, prepare
from papiq_migration.state import State

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PATHS = {
    "user": "/users",
    "contact": "/contacts",
    "document_type": "/document-types",
    "tag": "/tags",
    "field": "/fields",
    "drawer": "/drawers",
}
# What every Paperless object kind is counted as in the state.
_ACCOUNTED = (
    ("user", "users"),
    ("group", "groups"),
    ("contact", "correspondents"),
    ("document_type", "document_types"),
    ("tag", "tags"),
    ("field", "custom_fields"),
    ("storage_path", "storage_paths"),
)


async def verify(
    config: Config,
    source: Paperless,
    target: Papiq,
    state: State,
    *,
    progress: Progress = lambda message: None,
) -> dict[str, Any]:
    started = time.monotonic()
    snapshot = await source.snapshot(limit=config.limit)
    me = (await target.me())["user"]
    progress(f"Paperless {snapshot.version}: {len(snapshot.documents)} documents")
    deviations: list[dict[str, Any]] = []

    def deviation(kind: str, id: Any, what: str, expected: Any = None, found: Any = None) -> None:
        deviations.append(
            {
                "kind": kind,
                "id": str(id),
                "what": what,
                "expected": _show(expected),
                "found": _show(found),
            }
        )

    # --- Paperless objects the state does not account for ------------------------------------
    sources = {
        "user": snapshot.users,
        "group": snapshot.groups,
        "contact": snapshot.correspondents,
        "document_type": snapshot.document_types,
        "tag": snapshot.tags,
        "field": snapshot.custom_fields,
        "storage_path": snapshot.storage_paths,
    }
    for kind, _ in _ACCOUNTED:
        handled = {item["source_id"]: item for item in state.objects(kind)}
        for obj in sources[kind]:
            handled_obj = handled.get(str(obj["id"]))
            if handled_obj is None:
                deviation(kind, obj["id"], "not handled by the migration")
            elif handled_obj["status"] == "failed":
                deviation(kind, obj["id"], "failed", found="; ".join(handled_obj["notes"]))

    # --- objects in Papiq ---------------------------------------------------------------------
    listed = {kind: await target.items(path) for kind, path in _PATHS.items()}
    by_id = {kind: {item["id"]: item for item in items} for kind, items in listed.items()}
    objects_checked = 0
    ids = Ids(admin=me["id"])
    mappings = {
        "user": ids.users,
        "contact": ids.contacts,
        "document_type": ids.document_types,
        "tag": ids.tags,
    }
    for kind, path in (
        ("user", "users"),
        ("contact", "contacts"),
        ("document_type", "types"),
        ("tag", "tags"),
    ):
        del path
        for entry in state.objects(kind):
            if entry["papiq_id"] is None:
                continue
            objects_checked += 1
            found = by_id[kind].get(entry["papiq_id"])
            if found is None:
                deviation(kind, entry["source_id"], "missing in Papiq", entry["name"])
                continue
            name = found["username"] if kind == "user" else found["name"]
            if norm(name) != norm(entry["name"]):
                deviation(kind, entry["source_id"], "name differs", entry["name"], name)
            mappings[kind][int(entry["source_id"])] = entry["papiq_id"]
            if kind == "user":
                ids.usernames[norm(entry["name"])] = entry["papiq_id"]
    for user in snapshot.users:
        found = by_id["user"].get(ids.users.get(int(user["id"])) or "")
        if found is not None and found.get("active") is not bool(user.get("is_active", True)):
            created = state.object("user", str(user["id"]))
            if created is not None and created[1] == "new":
                deviation(
                    "user",
                    user["id"],
                    "active flag differs",
                    user.get("is_active"),
                    found.get("active"),
                )
    ids.usernames[norm(me["username"])] = me["id"]
    fields = {int(item["id"]): item for item in snapshot.custom_fields}
    for entry in state.objects("field"):
        if entry["papiq_id"] is None:
            continue
        objects_checked += 1
        found = by_id["field"].get(entry["papiq_id"])
        if found is None:
            deviation("field", entry["source_id"], "missing in Papiq", entry["name"])
            continue
        key = (
            entry["source_id"]
            if entry["source_id"] in ("asn", "notes")
            else field_key(int(entry["source_id"]))
        )
        ids.fields[key] = entry["papiq_id"]
        if entry["source_id"] in ("asn", "notes"):
            ids.specs[key] = ASN_SPEC if key == "asn" else NOTES_SPEC
        else:
            spec, _ = field_spec(fields[int(entry["source_id"])])
            if spec is not None:
                ids.specs[key] = spec
                if found["data_type"] != spec.data_type:
                    deviation(
                        "field",
                        entry["source_id"],
                        "type differs",
                        spec.data_type,
                        found["data_type"],
                    )
                missing = [c for c in spec.choices if c not in found.get("choices", [])]
                if missing:
                    deviation("field", entry["source_id"], "options missing", missing)

    types = {field_id: found["data_type"] for field_id, found in by_id["field"].items()}
    users = {int(user["id"]): user for user in snapshot.users}
    expected: dict[int, Prepared] = {
        int(document["id"]): prepare(
            document, ids=ids, users=users, custom_fields=fields, currency=config.currency
        )
        for document in snapshot.documents
    }
    # Drawers: each one needed exists with the shares it should have.
    needed: dict[str, Prepared] = {}
    for item in expected.values():
        key = item.drawer_key()
        if key is not None and item.skip is None:
            needed.setdefault(key, item)
    defaults = {d["owner_id"]: d["id"] for d in listed["drawer"] if d.get("is_default")}
    drawer_ids: dict[str, str] = {}
    for key, item in needed.items():
        objects_checked += 1
        known = state.object("drawer", key)
        found = by_id["drawer"].get(known[0] or "") if known else None
        if found is None:
            deviation("drawer", drawer_name(item.shares), "missing in Papiq")
            continue
        drawer_ids[key] = found["id"]
        have = {share["user_id"]: share["level"] for share in found.get("shares") or []}
        want = {ids.usernames[norm(name)]: level for name, level in item.shares}
        if have != want or found["owner_id"] != item.owner:
            deviation("drawer", found["name"], "owner or shares differ", want, have)

    # --- documents -----------------------------------------------------------------------------
    papiq_documents: dict[str, dict[str, Any]] = {}
    cursor = None
    while True:
        page = await target.documents(cursor=cursor)
        papiq_documents.update({item["id"]: item for item in page["items"]})
        cursor = page.get("next_cursor")
        if not cursor:
            break
    progress(f"{len(papiq_documents)} documents in Papiq")
    rows = {row.source_id: row for row in state.documents()}
    findings: list[dict[str, Any]] = []
    omitted: Counter[str] = Counter()
    checked = 0
    originals = {int(d["id"]): d for d in snapshot.documents}
    for document_id, item in expected.items():
        row = rows.get(document_id)
        if row is None:
            deviation("document", document_id, "not handled by the migration", item.title)
            continue
        if row.status == "skipped":
            omitted["documents not taken over"] += 1
            continue
        if row.status in ("failed", "uploaded", "planned") or row.papiq_id is None:
            deviation(
                "document", document_id, f"not migrated ({row.status})", item.title, row.reason
            )
            continue
        checked += 1
        actual = papiq_documents.get(row.papiq_id)
        if actual is None:
            deviation("document", document_id, "missing in Papiq", item.title)
            continue
        applied = _compare(
            item,
            actual,
            drawer_ids.get(key) if (key := item.drawer_key()) else defaults.get(item.owner),
            deviation,
            originals[document_id],
            row.sha256,
            types,
        )
        if actual.get("lane") != row.lane and row.status == "done":
            deviation(
                "document",
                document_id,
                "lane changed since the migration",
                row.lane,
                actual.get("lane"),
            )
        if actual.get("lane") in ("yellow", "red"):
            reason = row.reason or ""
            if not applied:
                reason += " (the metadata from Paperless is applied after a retry of the step)"
            findings.append(
                {
                    "id": document_id,
                    "title": item.title,
                    "lane": actual["lane"],
                    "reason": reason.strip(),
                }
            )
    extra = len(papiq_documents) - checked
    if extra > 0:
        omitted["documents in Papiq that are not from this migration"] = extra
    if config.rehash:
        await _rehash(config, source, expected, rows, papiq_documents, deviation, progress)
    omitted["storage paths"] = len(snapshot.storage_paths)
    for name, count in snapshot.counted.items():
        omitted[name.replace("_", " ")] = count
    return {
        "command": "verify",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "paperless": {"url": public(config.paperless_url), "version": snapshot.version},
        "papiq": {"url": public(config.papiq_url)},
        "checked": checked,
        "objects_checked": objects_checked,
        "deviations": deviations,
        "omitted": {k: v for k, v in omitted.items() if v},
        "findings": findings,
        "seconds": {"total": round(time.monotonic() - started, 1)},
    }


def _compare(
    item: Prepared,
    actual: dict[str, Any],
    drawer_id: str | None,
    deviation: Any,
    original: dict[str, Any],
    state_sha: str | None,
    types: dict[str, str],
) -> bool:
    """Compare a document with what it should be; False if its pipeline stopped before the
    metadata was applied (then there is nothing to compare: the document is a finding)."""
    id, meta = item.id, item.metadata
    outcomes = (actual.get("processing") or {}).get("outcomes") or {}
    classified, extracted = "classify" in outcomes, "extract_fields" in outcomes
    if classified:
        if actual["title"] != meta["title"]:
            deviation("document", id, "title differs", meta["title"], actual["title"])
        for field in ("contact_id", "document_type_id"):
            if (actual.get(field) or None) != (meta[field] or None):
                deviation("document", id, f"{field} differs", meta[field], actual.get(field))
        if sorted(actual.get("tag_ids") or []) != sorted(meta["tag_ids"]):
            deviation("document", id, "tags differ", meta["tag_ids"], actual.get("tag_ids"))
        if actual.get("document_date") != meta["document_date"]:
            deviation(
                "document",
                id,
                "document date differs",
                meta["document_date"],
                actual.get("document_date"),
            )
    if extracted:
        for field_id in sorted(set(meta["fields"]) | set(actual.get("fields") or {})):
            want, have = (
                meta["fields"].get(field_id),
                (actual.get("fields") or {}).get(field_id),
            )
            if not _same(want, have, types.get(field_id, "text")):
                deviation("document", id, f"field {field_id} differs", want, have)
    if actual["owner_id"] != item.owner:
        deviation(
            "document",
            id,
            "owner differs",
            item.owner_name or "executing admin",
            actual["owner_id"],
        )
    if drawer_id is not None and actual["drawer_id"] != drawer_id:
        deviation("document", id, "drawer differs", drawer_id, actual["drawer_id"])
    if actual.get("channel") != "migration" and state_sha is not None:
        deviation(
            "document",
            id,
            "was not taken over by the migration",
            "migration",
            actual.get("channel"),
        )
    checksum = _paperless_checksum(original) or state_sha
    if checksum and actual.get("sha256") != checksum:
        deviation("document", id, "SHA-256 of the original differs", checksum, actual.get("sha256"))
    return classified and extracted


def _paperless_checksum(document: dict[str, Any]) -> str | None:
    """Paperless keeps the SHA-256 of the original per version; the root version is the file."""
    for version in document.get("versions") or []:
        checksum = str(version.get("checksum") or "")
        if version.get("is_root") and _SHA256.fullmatch(checksum):
            return checksum
    return None


def _same(want: Any, have: Any, data_type: str) -> bool:
    """Field values are equal; numbers and amounts by value (`12` and `1.2E+1`)."""
    if want is None or have is None:
        return want is None and have is None
    try:
        if data_type == "amount" and isinstance(want, dict) and isinstance(have, dict):
            return want.get("currency") == have.get("currency") and Decimal(
                str(want.get("amount"))
            ) == Decimal(str(have.get("amount")))
        if data_type == "number":
            return Decimal(str(want)) == Decimal(str(have))
    except InvalidOperation:
        return False
    return bool(want == have)


async def _rehash(
    config: Config,
    source: Paperless,
    expected: dict[int, Prepared],
    rows: dict[int, Any],
    papiq_documents: dict[str, dict[str, Any]],
    deviation: Any,
    progress: Progress,
) -> None:
    """Download every original from Paperless again and compare its hash with Papiq's."""
    gate = asyncio.Semaphore(config.concurrency)

    async def one(document_id: int) -> None:
        row = rows.get(document_id)
        if row is None or row.papiq_id not in papiq_documents:
            return
        async with gate:
            with tempfile.TemporaryDirectory(prefix="papiq-verify-") as directory:
                download = await source.download_original(document_id, Path(directory) / "file")
        if download.sha256 != papiq_documents[row.papiq_id].get("sha256"):
            deviation(
                "document",
                document_id,
                "SHA-256 of the downloaded original differs",
                download.sha256,
            )

    await asyncio.gather(
        *(one(document_id) for document_id, item in expected.items() if item.skip is None)
    )
    progress("originals compared")


def _show(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 200 else text[:197] + "..."

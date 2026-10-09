"""The commands end to end against stand-ins for both APIs."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from tests import fake_papiq
from tests.conftest import Setup

PAPERLESS_KEY = "paperless-test-key"


def by_title(setup: Setup, title: str) -> dict[str, Any]:
    return next(d for d in setup.papiq.documents.values() if d["title"] == title)


def named(items: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    return next(i for i in items.values() if i.get("name", i.get("username")) == name)


# --- trial run --------------------------------------------------------------------------------


async def test_the_trial_run_writes_nothing_and_reports_everything(setup: Setup) -> None:
    assert await setup.run("plan") == 0
    assert setup.papiq.writes() == []
    assert all(method == "GET" for method, _ in setup.paperless.requests)
    assert setup.paperless.downloads == []
    assert not (setup.directory / "state.sqlite").exists()

    report = setup.report("plan")
    assert report["source_counts"] == {
        "documents": 10,
        "users": 4,
        "groups": 1,
        "correspondents": 3,
        "document_types": 1,
        "tags": 2,
        "custom_fields": 11,
        "storage_paths": 1,
        "saved_views": 2,
        "workflows": 1,
        "mail_rules": 0,
        "mail_accounts": 0,
        "share_links": 0,
        "trash": 0,
    }
    results = report["document_results"]
    assert results == {"planned": 8, "skipped": 2}
    text = (setup.directory / "reports" / "plan.md").read_text()
    assert "Trial run" in text and "message/rfc822" in text and "trash" in text
    assert "documents [11] is not taken over" in text  # the link field of document 10
    assert "the hierarchy (parent 1) is not taken over" in text
    assert "same name as" in text  # "acme energy" merges into "ACME Energy"
    assert PAPERLESS_KEY not in text and fake_papiq.TOKEN not in text


async def test_the_trial_run_marks_what_exists_in_papiq(setup: Setup) -> None:
    setup.papiq.contacts["c1"] = {"id": "c1", "name": "ACME ENERGY"}
    setup.papiq.users["u1"] = {"id": "u1", "username": "Anna", "role": "user", "active": True}
    assert await setup.run("plan") == 0
    report = setup.report("plan")
    users = {u["name"]: u for u in report["objects"]["user"]}
    assert users["anna"]["status"] == "existing" and users["anna"]["papiq_id"] == "u1"
    assert users["bob"]["status"] == "new"
    contacts = {c["source_id"]: c for c in report["objects"]["contact"]}
    assert contacts["1"]["status"] == contacts["2"]["status"] == "existing"
    assert contacts["3"]["status"] == "new"
    assert setup.papiq.writes() == []


async def test_a_wrong_key_stops_with_an_error(setup: Setup) -> None:
    assert await setup.run("plan", paperless_token="wrong") == 2
    assert await setup.run("run", papiq_token="wrong") == 2


async def test_a_read_only_papiq_key_cannot_run(setup: Setup) -> None:
    setup.papiq.scope = "read"
    assert await setup.run("run") == 2
    assert await setup.run("plan") == 0  # reading is enough for a trial run
    setup.papiq.scope = "read_write"
    setup.papiq.role = "user"
    assert await setup.run("run") == 2


# --- the migration ----------------------------------------------------------------------------


async def test_the_migration_takes_everything_over(setup: Setup) -> None:
    assert await setup.run("run") == 0
    papiq = setup.papiq

    # users: created without password, the inactive one deactivated
    assert {u["username"] for u in papiq.users.values()} == {
        "root",
        "tobias",
        "anna",
        "bob",
        "gone",
    }
    assert named(papiq.users, "gone")["active"] is False and named(papiq.users, "bob")["active"]
    # master data: names are unique regardless of case
    assert sorted(c["name"] for c in papiq.contacts.values()) == ["ACME Energy", "Unused"]
    assert [t["name"] for t in papiq.document_types.values()] == ["Invoice"]
    assert sorted(t["name"] for t in papiq.tags.values()) == ["tax", "tax/2024"]
    # fields: one per usable custom field, plus ASN and notes; the link field is left out
    types = {a["name"]: a["data_type"] for a in papiq.fields.values()}
    assert types == {
        "Text": "text",
        "Long": "text",
        "Site": "link",
        "Due": "date",
        "Paid": "boolean",
        "Count": "number",
        "Ratio": "number",
        "Net": "amount",
        "Gross": "amount",
        "Kind": "choice",
        "ASN": "number",
        "Notizen": "text",
    }
    assert named(papiq.fields, "Kind")["choices"] == ["One", "Two"]

    # documents: the e-mail and the one in the trash stay behind
    assert len(papiq.documents) == 8
    first = by_title(setup, "Document 10")
    assert first["channel"] == "migration"
    assert first["owner_id"] == named(papiq.users, "tobias")["id"]
    assert first["document_date"] == "2024-05-01"
    assert first["contact_id"] == named(papiq.contacts, "ACME Energy")["id"]
    assert first["document_type_id"] == named(papiq.document_types, "Invoice")["id"]
    assert len(first["tag_ids"]) == 2
    values = {papiq.fields[k]["name"]: v for k, v in first["fields"].items()}
    assert values == {
        "Text": "Hello",
        "Long": "Some long text",
        "Site": "https://example.org/x",
        "Due": "2024-06-30",
        "Paid": True,
        "Count": "42",
        "Ratio": "1.5",
        "Net": {"amount": "12.50", "currency": "USD"},
        "Gross": {"amount": "99.90", "currency": "EUR"},
        "Kind": "Two",
        "ASN": "77",
        "Notizen": "2024-01-01, tobias: First\n\n2024-02-02, anna: Second",
    }
    # an invalid URL is left out, the rest of that document is taken over
    second = by_title(setup, "Document 11")
    assert second["fields"] == {}
    assert second["contact_id"] == named(papiq.contacts, "ACME Energy")["id"]
    # no owner, or an owner who is gone: the executing admin
    assert by_title(setup, "Document 14")["owner_id"] == fake_papiq.ADMIN["id"]
    assert by_title(setup, "Document 15")["owner_id"] == fake_papiq.ADMIN["id"]
    # documents with a negative amount and an unknown option
    nineteen = by_title(setup, "Document 19")
    assert {papiq.fields[k]["name"] for k in nineteen["fields"]} == {"Net"}

    # drawers: no extra permissions, default drawer; otherwise one shared drawer per combination
    assert by_title(setup, "Document 12")["drawer_id"].startswith("default:")
    assert by_title(setup, "Document 16")["drawer_id"].startswith("default:")
    shared = by_title(setup, "Document 13")
    drawer = papiq.drawers[shared["drawer_id"]]
    assert drawer["name"] == "Geteilt: bob (lesen), tobias (schreiben)"
    assert drawer["owner_id"] == named(papiq.users, "anna")["id"]
    assert {(s["user_id"], s["level"]) for s in drawer["shares"]} == {
        (named(papiq.users, "bob")["id"], "read"),
        (named(papiq.users, "tobias")["id"], "read_write"),
    }
    assert sum(1 for d in papiq.drawers.values() if not d["is_default"]) == 1

    # the upload carries owner, channel and metadata, and no key
    upload = next(u for u in papiq.uploads if u["filename"] == "scan-13.pdf")
    assert upload["channel"] == "migration" and upload["owner"] == drawer["owner_id"]
    assert upload["drawer_id"] == drawer["id"]
    assert json.loads(upload["metadata"])["title"] == "Document 13"
    unowned = next(u for u in papiq.uploads if u["filename"] == "scan-14.pdf")
    assert unowned["owner"] == fake_papiq.ADMIN["id"]


async def test_the_report_accounts_for_every_paperless_object(setup: Setup) -> None:
    assert await setup.run("run") == 0
    report = setup.report("run")
    counts: dict[str, int] = report["source_counts"]
    objects: dict[str, list[dict[str, Any]]] = report["objects"]
    assert len(objects["user"]) == counts["users"]
    assert len(objects["group"]) == counts["groups"]
    assert len(objects["contact"]) == counts["correspondents"]
    assert len(objects["document_type"]) == counts["document_types"]
    assert len(objects["tag"]) == counts["tags"]
    assert len(objects["field"]) == counts["custom_fields"] + 2
    assert len(objects["storage_path"]) == counts["storage_paths"]
    assert len(report["documents"]) == counts["documents"]
    omitted = [o for o in objects["field"] if o["status"] == "omitted"]
    assert [o["name"] for o in omitted] == ["Related"]
    assert report["document_results"] == {"done": 8, "skipped": 2}
    assert report["lanes"] == {"green": 8}
    document = next(d for d in report["documents"] if d["id"] == 17)
    assert document["status"] == "skipped" and "message/rfc822" in document["reason"]
    text = (setup.directory / "reports" / "run.md").read_text()
    assert "## All documents" in text and "Document 13" in text


async def test_no_key_ends_up_in_a_file(setup: Setup) -> None:
    assert await setup.run("run") == 0
    assert await setup.run("verify") == 0
    for path in setup.directory.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert PAPERLESS_KEY.encode() not in data and fake_papiq.TOKEN.encode() not in data, (
                path
            )


async def test_a_second_run_creates_nothing_twice(setup: Setup) -> None:
    assert await setup.run("run") == 0
    snapshot = {
        name: len(getattr(setup.papiq, name))
        for name in ("users", "contacts", "tags", "fields", "drawers", "documents")
    }
    uploads, writes = len(setup.papiq.uploads), len(setup.papiq.writes())
    downloads = len(setup.paperless.downloads)
    assert await setup.run("run") == 0
    assert len(setup.papiq.uploads) == uploads
    assert len(setup.paperless.downloads) == downloads
    assert len(setup.papiq.writes()) == writes
    assert snapshot == {
        name: len(getattr(setup.papiq, name))
        for name in ("users", "contacts", "tags", "fields", "drawers", "documents")
    }
    report = setup.report("run")
    # What the first run created still reads as created.
    assert {u["status"] for u in report["objects"]["user"]} == {"new"}


async def test_a_new_state_file_finds_what_is_there(setup: Setup) -> None:
    assert await setup.run("run") == 0
    count = len(setup.papiq.documents)
    assert await setup.run("run", state=setup.directory / "other.sqlite") == 0
    # Master data is matched by name; every file is a duplicate of the same owner.
    assert len(setup.papiq.documents) == count
    assert len(setup.papiq.users) == 5 and len(setup.papiq.contacts) == 2
    report = setup.report("run")
    assert report["document_results"] == {"duplicate": 8, "skipped": 2}
    assert await setup.run("verify", state=setup.directory / "other.sqlite") == 0


async def test_a_state_belongs_to_one_source_and_target(setup: Setup) -> None:
    assert await setup.run("run") == 0
    assert await setup.run("run", papiq_url="http://elsewhere") == 2


# --- interruption and failures ----------------------------------------------------------------


async def test_a_stopped_run_continues_where_it_stopped(setup: Setup) -> None:
    stop = asyncio.Event()

    def after(count: int) -> None:
        if count == 3:
            stop.set()

    setup.papiq.after_upload = after
    assert await setup.run("run", stop=stop, concurrency=1) == 130
    stopped = len(setup.papiq.documents)
    assert 3 <= stopped < 8
    first_uploads = list(setup.papiq.uploads)

    setup.papiq.after_upload = None
    assert await setup.run("run") == 0
    assert len(setup.papiq.documents) == 8
    sent = [u["filename"] for u in setup.papiq.uploads]
    assert len(sent) == len(set(sent)) == 8  # nothing twice
    assert setup.papiq.uploads[: len(first_uploads)] == first_uploads
    assert await setup.run("verify") == 0


async def test_an_upload_in_progress_is_waited_for_after_the_restart(setup: Setup) -> None:
    setup.papiq.ticks = -1  # the pipeline never finishes
    assert await setup.run("run", pipeline_timeout=0.0) == 1
    rows = (
        sqlite3.connect(setup.directory / "state.sqlite")
        .execute("SELECT status, count(*) FROM documents GROUP BY status")
        .fetchall()
    )
    assert dict(rows) == {"uploaded": 8, "skipped": 2}
    setup.papiq.ticks = 1
    assert await setup.run("run") == 0
    assert len(setup.papiq.documents) == 8
    assert len(setup.papiq.uploads) == 8  # waiting again, not uploading again
    assert setup.report("run")["document_results"] == {"done": 8, "skipped": 2}


async def test_a_server_error_is_repeated_and_a_refusal_is_reported(setup: Setup) -> None:
    setup.papiq.errors["scan-10.pdf"] = (503, 2)
    setup.papiq.errors["scan-11.pdf"] = (422, 99)
    assert await setup.run("run") == 1
    assert len(setup.papiq.documents) == 7
    assert by_title(setup, "Document 10")
    report = setup.report("run")
    failed = [d for d in report["documents"] if d["status"] == "failed"]
    assert [d["id"] for d in failed] == [11] and "422" in failed[0]["reason"]
    assert "Documents that failed" in (setup.directory / "reports" / "run.md").read_text()
    # Fix the cause and run again: only that document is sent.
    setup.papiq.errors.clear()
    sent = len(setup.papiq.uploads)
    assert await setup.run("run") == 0
    assert len(setup.papiq.uploads) == sent + 1 and len(setup.papiq.documents) == 8


async def test_a_duplicate_is_mapped_not_counted_as_an_error(setup: Setup) -> None:
    assert await setup.run("run") == 0
    existing = by_title(setup, "Document 12")
    # A second document with the same file and owner in Paperless (the same bytes).
    setup.archive.documents.append(
        {**setup.archive.documents[2], "id": 30, "title": "Same file again"}
    )
    setup.archive.files[30] = setup.archive.files[12]
    assert await setup.run("run") == 0
    row = next(d for d in setup.report("run")["documents"] if d["id"] == 30)
    assert row["status"] == "duplicate" and row["papiq_id"] == existing["id"]
    assert len(setup.papiq.documents) == 8


async def test_a_file_papiq_refuses_is_reported_as_not_taken_over(setup: Setup) -> None:
    setup.archive.files[10] = b"not a pdf"
    assert await setup.run("run") == 0
    row = next(d for d in setup.report("run")["documents"] if d["id"] == 10)
    assert row["status"] == "skipped" and "unsupported" in row["reason"]


async def test_documents_that_are_not_green_are_reported_with_the_reason(setup: Setup) -> None:
    setup.papiq.lanes["scan-12.pdf"] = "red"
    assert await setup.run("run") == 0
    report = setup.report("run")
    assert report["lanes"] == {"green": 7, "red": 1}
    row = next(d for d in report["documents"] if d["id"] == 12)
    assert row["lane"] == "red" and row["reason"] == "parse: no text recognised"
    assert "Documents in yellow or red" in (setup.directory / "reports" / "run.md").read_text()
    verified = await setup.run("verify")
    assert verified == 0  # a red document is a finding, not a deviation
    assert setup.report("verify")["findings"][0]["id"] == 12


async def test_only_the_first_documents_by_id_with_a_limit(setup: Setup) -> None:
    assert await setup.run("run", limit=3) == 0
    assert sorted(d["title"] for d in setup.papiq.documents.values()) == [
        "Document 10",
        "Document 11",
        "Document 12",
    ]
    assert await setup.run("run") == 0
    assert len(setup.papiq.documents) == 8


# --- the check --------------------------------------------------------------------------------


async def test_the_check_finds_no_deviation_after_a_migration(setup: Setup) -> None:
    assert await setup.run("run") == 0
    assert await setup.run("verify") == 0
    report = setup.report("verify")
    assert report["deviations"] == [] and report["checked"] == 8
    assert report["omitted"]["documents not taken over"] == 2
    assert "no deviation" in (setup.directory / "reports" / "verify.md").read_text()


async def test_the_check_reports_every_difference(setup: Setup) -> None:
    assert await setup.run("run") == 0
    document = by_title(setup, "Document 10")
    document["title"] = "Changed"
    document["tag_ids"] = []
    document["owner_id"] = "someone-else"
    document["sha256"] = "0" * 64
    gone = by_title(setup, "Document 12")
    del setup.papiq.documents[gone["id"]]
    named(setup.papiq.contacts, "Unused")["name"] = "Renamed"
    del setup.papiq.tags[next(iter(setup.papiq.tags))]
    assert await setup.run("verify") == 1
    deviations = setup.report("verify")["deviations"]
    what = {(d["kind"], d["what"]) for d in deviations}
    assert ("document", "title differs") in what
    assert ("document", "tags differ") in what
    assert ("document", "owner differs") in what
    assert ("document", "SHA-256 of the original differs") in what
    assert ("document", "missing in Papiq") in what
    assert ("contact", "name differs") in what
    assert ("tag", "missing in Papiq") in what


async def test_the_check_notices_what_was_never_handled(setup: Setup) -> None:
    assert await setup.run("run", limit=3) == 0
    assert await setup.run("verify") == 1  # the other documents were not handled
    deviations = setup.report("verify")["deviations"]
    documents = [d for d in deviations if d["kind"] == "document"]
    assert len(documents) == 7
    assert all(d["what"] == "not handled by the migration" for d in documents)


async def test_rehashing_downloads_the_originals_again(setup: Setup) -> None:
    assert await setup.run("run") == 0
    before = len(setup.paperless.downloads)
    assert await setup.run("verify", rehash=True) == 0
    assert len(setup.paperless.downloads) == before + 8
    next(iter(setup.papiq.documents.values()))["sha256"] = "1" * 64
    assert await setup.run("verify", rehash=True) == 1


def test_the_state_file_is_not_a_secret_store(setup: Setup) -> None:
    assert not Path(setup.directory / "state.sqlite").exists()


async def test_the_check_compares_numbers_by_value_and_skips_what_a_stopped_pipeline_never_applied(
    setup: Setup,
) -> None:
    setup.papiq.lanes["scan-12.pdf"] = "red"
    assert await setup.run("run") == 0
    gross = named(setup.papiq.fields, "Gross")["id"]
    document = by_title(setup, "Document 10")
    document["fields"][gross] = {"amount": "9.99E+1", "currency": "EUR"}  # 99.90
    count = named(setup.papiq.fields, "Count")["id"]
    document["fields"][count] = "4.2E+1"  # 42
    stopped = by_title(setup, "Document 12")
    stopped["title"] = "scan-12"  # the pipeline stopped before the metadata was applied
    stopped["processing"]["outcomes"] = {}
    assert await setup.run("verify") == 0
    findings = setup.report("verify")["findings"]
    assert findings[0]["id"] == 12 and "applied after a retry" in findings[0]["reason"]
    # A wrong amount is still found.
    document["fields"][gross] = {"amount": "9.9E+1", "currency": "EUR"}
    assert await setup.run("verify") == 1

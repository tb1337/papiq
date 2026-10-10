"""Edge cases found in review: inactive owners, shares with the executing admin, drawers that
cannot be made, lost answers, big values, what the check says about default drawers."""

import json
from typing import Any

from papiq_migration.config import public
from papiq_migration.fake_paperless import pdf
from tests.conftest import PDF, Setup
from tests.fake_papiq import ADMIN


def add_document(setup: Setup, id: int, owner: int | None, **changes: Any) -> None:
    template = setup.archive.documents[2]
    setup.archive.documents.append(
        {
            **template,
            "id": id,
            "title": f"Extra {id}",
            "owner": owner,
            "original_file_name": f"extra-{id}.pdf",
            **changes,
        }
    )
    setup.archive.files[id] = pdf(id, PDF)


def titled(setup: Setup, title: str) -> dict[str, Any]:
    return next(d for d in setup.papiq.documents.values() if d["title"] == title)


async def test_documents_of_an_inactive_user_are_taken_over_and_the_user_deactivated(
    setup: Setup,
) -> None:
    # "gone" (id 4) is inactive in Paperless and owns a document that anna may read.
    add_document(
        setup,
        30,
        4,
        permissions={"view": {"users": [2], "groups": []}, "change": {"users": [], "groups": []}},
    )
    assert await setup.run("run") == 0
    document = titled(setup, "Extra 30")
    gone = next(u for u in setup.papiq.users.values() if u["username"] == "gone")
    assert document["owner_id"] == gone["id"] and gone["active"] is False
    assert setup.papiq.drawers[document["drawer_id"]]["owner_id"] == gone["id"]
    # A second run wakes nobody up and writes nothing.
    writes = len(setup.papiq.writes())
    assert await setup.run("run") == 0
    assert len(setup.papiq.writes()) == writes
    assert await setup.run("verify") == 0


async def test_a_run_that_failed_for_an_inactive_owner_wakes_the_user_for_the_retry(
    setup: Setup,
) -> None:
    add_document(setup, 30, 4)
    setup.papiq.errors["extra-30.pdf"] = (422, 99)
    assert await setup.run("run") == 1
    gone = next(u for u in setup.papiq.users.values() if u["username"] == "gone")
    assert gone["active"] is False
    setup.papiq.errors.clear()
    assert await setup.run("run") == 0
    assert titled(setup, "Extra 30")["owner_id"] == gone["id"]
    assert gone["active"] is False


async def test_a_share_with_the_executing_admin_is_dropped_not_fatal(setup: Setup) -> None:
    setup.archive.users.append(
        {
            "id": 5,
            "username": ADMIN["username"],
            "is_active": True,
            "is_superuser": True,
            "groups": [],
        }
    )
    # No owner: the executing admin owns the document, and the permissions name the admin.
    add_document(
        setup,
        31,
        None,
        permissions={
            "view": {"users": [5, 3], "groups": []},
            "change": {"users": [], "groups": []},
        },
    )
    assert await setup.run("run") == 0
    document = titled(setup, "Extra 31")
    assert document["owner_id"] == ADMIN["id"]
    drawer = setup.papiq.drawers[document["drawer_id"]]
    assert [s["level"] for s in drawer["shares"]] == ["read"]  # bob only
    row = next(d for d in setup.report("run")["documents"] if d["id"] == 31)
    assert any("owns the document" in note for note in row["notes"])


async def test_a_drawer_that_cannot_be_made_fails_its_documents_only(setup: Setup) -> None:
    setup.papiq.refuse["POST /drawers"] = 422
    assert await setup.run("run") == 1
    report = setup.report("run")
    failed = [d for d in report["documents"] if d["status"] == "failed"]
    assert [d["id"] for d in failed] == [13] and "drawer" in failed[0]["reason"]
    assert len(setup.papiq.documents) == 7
    assert [d["status"] for d in report["objects"]["drawer"]] == ["failed"]
    setup.papiq.refuse.clear()
    assert await setup.run("run") == 0
    assert len(setup.papiq.documents) == 8


async def test_a_lost_answer_after_creating_a_user_is_not_an_error(setup: Setup) -> None:
    setup.papiq.lose_answer = {"anna", "ACME Energy"}
    assert await setup.run("run") == 0
    names = [u["username"] for u in setup.papiq.users.values()]
    assert names.count("anna") == 1
    assert [c["name"] for c in setup.papiq.contacts.values()].count("ACME Energy") == 1


async def test_a_refusal_that_stops_the_migration_still_leaves_a_report(setup: Setup) -> None:
    setup.papiq.refuse["POST /users"] = 422
    assert await setup.run("run") == 2
    report = setup.report("run")
    assert {u["status"] for u in report["objects"]["user"]} == {"failed"}
    assert setup.papiq.documents == {}


async def test_a_huge_value_is_left_out_not_the_whole_document(setup: Setup) -> None:
    setup.archive.documents[0]["custom_fields"].append({"field": 2, "value": "x" * 60_000})
    assert await setup.run("run") == 0
    document = titled(setup, "Document 10")
    assert len(json.dumps(document["fields"])) < 45_000
    row = next(d for d in setup.report("run")["documents"] if d["id"] == 10)
    assert any("too large" in note for note in row["notes"])


async def test_what_paperless_has_beyond_documents_is_counted(setup: Setup) -> None:
    setup.archive.trash, setup.archive.mail_accounts = 3, 2
    assert await setup.run("plan") == 0
    others = {o["source_id"]: o for o in setup.report("plan")["objects"]["other"]}
    assert others["trash"]["notes"] == ["3 not taken over"]
    assert others["mail_accounts"]["notes"] == ["2 not taken over"]
    assert others["date_added"]["status"] == others["history"]["status"] == "omitted"


async def test_further_versions_of_a_file_are_reported(setup: Setup) -> None:
    setup.archive.documents[0]["versions"] = [
        {"id": 10, "is_root": True, "checksum": "x"},
        {"id": 99, "is_root": False, "checksum": "y"},
    ]
    assert await setup.run("run") == 0
    row = next(d for d in setup.report("run")["documents"] if d["id"] == 10)
    assert any("further versions" in note for note in row["notes"])


async def test_the_check_looks_at_the_default_drawer_too(setup: Setup) -> None:
    assert await setup.run("run") == 0
    document = titled(setup, "Document 12")  # anna's, in her default drawer
    assert await setup.run("verify") == 0
    document["drawer_id"] = f"default:{ADMIN['id']}"  # a rule or a person filed it elsewhere
    assert await setup.run("verify") == 1
    assert "drawer differs" in {d["what"] for d in setup.report("verify")["deviations"]}


async def test_a_duplicate_gets_its_lane_too(setup: Setup) -> None:
    assert await setup.run("run") == 0
    assert await setup.run("run", state=setup.directory / "other.sqlite") == 0
    lanes = {d["lane"] for d in setup.report("run")["documents"] if d["status"] == "duplicate"}
    assert lanes == {"green"}


def test_addresses_lose_their_credentials() -> None:
    assert public("https://user:secret@host:8000/base?x=1") == "https://host:8000/base"
    assert public("http://localhost:8765") == "http://localhost:8765"
    assert public(None) is None


async def test_a_run_notes_that_a_document_was_repaired_since(setup: Setup) -> None:
    setup.papiq.lanes["scan-12.pdf"] = "red"
    assert await setup.run("run") == 0
    assert setup.report("run")["lanes"] == {"green": 7, "red": 1}
    document = titled(setup, "Document 12")
    document["_lane"] = "green"  # retried and fixed in Papiq
    assert await setup.run("run") == 0
    report = setup.report("run")
    assert report["lanes"] == {"green": 8}
    assert next(d for d in report["documents"] if d["id"] == 12)["reason"] is None

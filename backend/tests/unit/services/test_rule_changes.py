"""The rules with trigger `change`, when a person changes a document's metadata, and the dry
run of such a change."""

from datetime import date

import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.pipeline import Lane, Outcome, ProcessingStatus
from papiq.core.domain.rule_engine import Note
from papiq.core.domain.rules import ForceReview, SetContact, SetDrawer, SetTitle, Trigger
from papiq.core.services.documents import MetadataChange
from papiq.core.services.inbox import RULES_CHANGE
from tests.unit.services.conftest import World
from tests.unit.services.rules_support import (
    Classifies,
    add_tags,
    all_of,
    channel_api,
    contact_is,
    definition,
    has_tag,
    rule_world,
)

CHANGE = (Trigger.CHANGE,)


def applied(change: MetadataChange) -> list[str]:
    assert change.rules is not None
    return [effect.field for effect in change.rules.plan.effects]


def reported(change: MetadataChange) -> list[Note]:
    assert change.rules is not None
    return [note for report in change.rules.plan.reports for note in report.notes]


async def test_a_change_rule_acts_when_it_starts_to_hold(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme, beta = await r.contact("ACME"), await r.contact("Beta")
    tag = await r.tag("acme")
    rule = await r.user_rule(
        owner,
        definition(
            "ACME", contact_is(acme.id), add_tags(tag.id), SetTitle("ACME bill"), triggers=CHANGE
        ),
    )
    document = await r.arrive(owner)
    assert (document.tag_ids, document.title) == (set(), "scan")  # not on arrival

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=beta.id)
    )
    assert change.rules is not None and change.rules.plan.reports == []

    # The person's title wins over the rule's; the rule's tag is set.
    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id, title="Mine")
    )
    assert applied(change) == ["tags"]
    assert reported(change) == [Note("title", "overruled", "a person decided this field")]
    stored = await r.stored(document)
    assert (stored.tag_ids, stored.title, stored.lane) == ({tag.id}, "Mine", Lane.GREEN)
    entry = (await r.rule_entries(document, RULES_CHANGE))[-1]
    assert entry.result.outcome is Outcome.OK
    assert entry.result.input == {
        "trigger": "change",
        "actor": str(owner.id),
        "changed": ["contact", "title"],
        "tags_added": [],
        "tags_removed": [],
        "rules": [{"id": str(rule.id), "version": 1}],
    }

    # It held before: another change does not apply it again.
    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(document_date=date(2026, 3, 1), tag_ids=frozenset())
    )
    assert change.rules is not None and change.rules.plan.reports == []
    assert (await r.stored(document)).tag_ids == set()

    # Once it stops holding, it can start to hold again; the tag the person removed stays
    # removed.
    await r.documents.change_metadata(owner.id, document.id, DocumentChanges(contact_id=None))
    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )
    assert change.rules is not None and len(change.rules.plan.reports) == 1
    assert Note("tags", "overruled", f"a person removed tag {tag.id}") in reported(change)
    assert (await r.stored(document)).tag_ids == set()


async def test_a_change_rule_keeps_what_the_person_decided_earlier(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    await r.user_rule(
        owner, definition("ACME", contact_is(acme.id), SetTitle("ACME bill"), triggers=CHANGE)
    )
    document = await r.arrive(owner)
    await r.documents.change_metadata(owner.id, document.id, DocumentChanges(title="Mine"))

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert applied(change) == []
    assert reported(change) == [Note("title", "overruled", "a person decided this field")]
    assert (await r.stored(document)).title == "Mine"


async def test_nothing_turns_yellow_on_a_change(world: World) -> None:
    """Conflicts, refused actions and forced reviews are only reported; a global rule never
    changes who sees the document."""
    r = await rule_world(world)
    owner, reader = await world.user(), await world.user()
    acme = await r.contact("ACME")
    paid, seen = await r.tag("paid"), await r.tag("seen")
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    # The model set the contact and no person confirmed it.
    document = await r.arrive(owner, classify=Classifies(contact=acme.id))
    assert (document.lane, document.contact_id) == (Lane.GREEN, acme.id)
    paid_now = has_tag(paid.id)
    await r.user_rule(
        owner,
        definition(
            "Shared", all_of(contact_is(acme.id), paid_now), SetDrawer(shared.id), triggers=CHANGE
        ),
    )
    await r.user_rule(owner, definition("A", paid_now, SetTitle("A"), triggers=CHANGE))
    await r.user_rule(owner, definition("B", paid_now, SetTitle("B"), triggers=CHANGE))
    await r.global_rule(
        definition("Audit", paid_now, ForceReview("audit"), add_tags(seen.id), triggers=CHANGE)
    )

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(tag_ids=frozenset({paid.id}))
    )

    kinds = {(note.field, note.kind) for note in reported(change)}
    assert kinds == {
        ("drawer", "refused"),
        ("title", "conflict"),
        ("review", "skipped"),
    }
    assert applied(change) == ["tags"]
    stored = await r.stored(document)
    assert stored.lane is Lane.GREEN
    assert stored.processing.status is ProcessingStatus.COMPLETED
    assert stored.drawer_id == (await world.default_drawer(owner)).id
    assert (stored.title, stored.tag_ids) == ("scan", {paid.id, seen.id})
    assert await world.documents.list_visible(reader.id) == []


async def test_tags_the_person_sets_in_the_change_are_trusted(world: World) -> None:
    r = await rule_world(world)
    owner, reader = await world.user(), await world.user()
    acme = await r.contact("ACME")
    tax = await r.tag("tax")
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    document = await r.arrive(owner, classify=Classifies(tags=[tax.id]))
    await r.user_rule(
        owner,
        definition(
            "Shared",
            all_of(has_tag(tax.id), contact_is(acme.id)),
            SetDrawer(shared.id),
            triggers=CHANGE,
        ),
    )

    # The person sets contact and tags; the tag the model proposed is now theirs.
    change = await r.documents.change_metadata(
        owner.id,
        document.id,
        DocumentChanges(contact_id=acme.id, tag_ids=frozenset({tax.id})),
    )

    assert reported(change) == []
    assert change.document.drawer_id == shared.id


async def test_change_rules_wait_until_processing_is_complete(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    tag = await r.tag("acme")
    await r.user_rule(owner, definition("Check", channel_api(), ForceReview("check")))
    await r.user_rule(
        owner, definition("ACME", contact_is(acme.id), add_tags(tag.id), triggers=CHANGE)
    )
    document = await r.arrive(owner)
    assert document.lane is Lane.YELLOW

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert change.rules is None
    assert change.document.tag_ids == set()
    (entry,) = await r.rule_entries(document, RULES_CHANGE)
    assert entry.result.input["changed"] == ["contact"]
    assert "rules" not in entry.result.input


async def test_rules_do_not_set_each_other_off(world: World) -> None:
    """A rule's change is no change by a person: it never sets off other rules, now or with
    a later change."""
    r = await rule_world(world)
    owner = await world.user()
    acme, beta = await r.contact("ACME"), await r.contact("Beta")
    flagged = await r.tag("flagged")
    await r.user_rule(
        owner, definition("Flag", contact_is(acme.id), add_tags(flagged.id), triggers=CHANGE)
    )
    await r.user_rule(
        owner, definition("Unflag", has_tag(flagged.id), SetContact(beta.id), triggers=CHANGE)
    )
    document = await r.arrive(owner)

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )
    assert applied(change) == ["tags"]
    change = await r.documents.change_metadata(owner.id, document.id, DocumentChanges(title="X"))
    assert change.rules is not None and change.rules.plan.reports == []

    stored = await r.stored(document)
    assert (stored.contact_id, stored.tag_ids, stored.title) == (acme.id, {flagged.id}, "X")


async def test_an_editor_sets_off_the_owners_rules(world: World) -> None:
    r = await rule_world(world)
    owner, editor = await world.user(), await world.user()
    acme = await r.contact("ACME")
    owners, editors, everyone = await r.tag("owner"), await r.tag("editor"), await r.tag("all")
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, editor.id, ShareLevel.READ_WRITE)
    mine = await r.user_rule(
        owner, definition("Owner", contact_is(acme.id), add_tags(owners.id), triggers=CHANGE)
    )
    await r.user_rule(
        editor, definition("Editor", contact_is(acme.id), add_tags(editors.id), triggers=CHANGE)
    )
    common = await r.global_rule(
        definition("All", contact_is(acme.id), add_tags(everyone.id), triggers=CHANGE)
    )
    document = await r.arrive(owner, drawer=shared.id)

    change = await r.documents.change_metadata(
        editor.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert change.rules is None  # the rules' report is the owner's
    assert change.access is ShareLevel.READ_WRITE
    assert (await r.stored(document)).tag_ids == {owners.id, everyone.id}
    (entry,) = await r.rule_entries(document, RULES_CHANGE)
    assert entry.result.input["actor"] == str(editor.id)
    assert entry.result.input["rules"] == [
        {"id": str(common.id), "version": 1},
        {"id": str(mine.id), "version": 1},
    ]


async def test_a_dry_run_stores_nothing_and_shows_the_caller_only_their_own(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, editor = await world.user(), await world.user()
    acme = await r.contact("ACME")
    filed, editors = await r.tag("filed"), await r.tag("editor")
    shared = await world.drawers.create(owner.id, "Shared")
    private = await world.drawers.create(owner.id, "Private")
    await world.drawers.share(owner.id, shared.id, editor.id, ShareLevel.READ_WRITE)
    await r.user_rule(
        owner,
        definition(
            "Private",
            contact_is(acme.id),
            SetDrawer(private.id),
            add_tags(filed.id),
            triggers=CHANGE,
        ),
    )
    await r.user_rule(
        editor, definition("Editor", contact_is(acme.id), add_tags(editors.id), triggers=CHANGE)
    )
    document = await r.arrive(owner, drawer=shared.id)
    log, events = await r.log(document), world.event_types()
    acme_now = DocumentChanges(contact_id=acme.id)

    # The editor would lose sight of the document: no access, no report of the owner's rules.
    tried = await r.documents.change_metadata(editor.id, document.id, acme_now, dry_run=True)
    assert tried.access is None
    assert tried.rules is None
    assert tried.document.tag_ids == {filed.id}  # the owner's rule, not the editor's

    # The owner sees what their rules would do.
    tried = await r.documents.change_metadata(owner.id, document.id, acme_now, dry_run=True)
    assert tried.access is ShareLevel.READ_WRITE
    assert tried.drawer.id == private.id
    assert tried.rules is not None
    assert [report.name for report in tried.rules.plan.reports] == ["Private"]
    assert tried.before is not None and tried.before.drawer_id == shared.id

    # Nothing was stored.
    assert await r.stored(document) == document
    assert await r.log(document) == log
    assert world.event_types() == events

    # The change itself takes the document out of the editor's sight.
    changed = await r.documents.change_metadata(editor.id, document.id, acme_now)
    assert changed.access is None
    with pytest.raises(NotFoundError):
        await r.documents.get(editor.id, document.id)
    assert (await r.stored(document)).drawer_id == private.id

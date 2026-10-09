"""The rules on arrival: the steps `apply_rules` and `file`, and confirming in the inbox."""

import logging

import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import OpenFieldsError
from papiq.core.domain.fields import FieldType
from papiq.core.domain.pipeline import Lane, Outcome, ProcessingStatus, Step
from papiq.core.domain.rules import (
    ForceReview,
    SetContact,
    SetDrawer,
    SetField,
    SetTitle,
    Trigger,
)
from papiq.core.services.inbox import PERSON, RULES
from papiq.core.services.rules.running import PatternBudget, prepare
from tests.unit.services.conftest import World
from tests.unit.services.rules_support import (
    Classifies,
    add_tags,
    channel_api,
    contact_is,
    definition,
    has_tag,
    incoming_pdf,
    notes,
    reports,
    rule_world,
    text_matches,
)

CHANGE = (Trigger.CHANGE,)


async def test_rules_act_on_arrival_and_the_log_names_rule_and_version(world: World) -> None:
    r = await rule_world(world)
    owner, stranger = await world.user(), await world.user()
    acme = await r.contact("ACME")
    tax, checked, foreign = await r.tag("tax"), await r.tag("checked"), await r.tag("foreign")
    note = await world.master_data.create_field(r.admin.id, "Note", FieldType.TEXT)
    household = await world.drawers.create(owner.id, "Household")
    rule = await r.user_rule(owner, definition("Draft", channel_api(), SetTitle("Draft")))
    rule = await r.rules.change(
        owner.id,
        rule.id,
        definition(
            "ACME",
            channel_api(),
            SetContact(acme.id),
            SetTitle("{contact} {filename}"),
            add_tags(tax.id),
            SetField(note.id, "from a rule"),
            SetDrawer(household.id),
        ),
    )
    shared = await r.global_rule(definition("Checked", channel_api(), add_tags(checked.id)))
    # Another user's rule never acts on the owner's documents.
    await r.user_rule(stranger, definition("Foreign", channel_api(), add_tags(foreign.id)))

    document = await r.arrive(owner, filename="Bill 2026.pdf")

    assert document.lane is Lane.GREEN
    assert (document.contact_id, document.title) == (acme.id, "ACME Bill 2026")
    assert document.tag_ids == {tax.id, checked.id}
    assert document.fields == {note.id: "from a rule"}
    assert document.drawer_id == household.id
    (entry,) = await r.rule_entries(document, RULES)
    assert entry.result.outcome is Outcome.OK
    assert entry.result.input == {
        "trigger": "ingest",
        "rules": [{"id": str(shared.id), "version": 1}, {"id": str(rule.id), "version": 2}],
    }
    by_rule = {report["rule_id"]: report for report in reports(entry.result.output)}
    assert by_rule[str(rule.id)]["version"] == 2
    assert by_rule[str(rule.id)]["name"] == "ACME"
    applied = by_rule[str(rule.id)]["applied"]
    assert isinstance(applied, list)
    assert {item["field"] for item in applied if isinstance(item, dict)} == {
        "contact",
        "title",
        "tags",
        f"field:{note.id}",
        "drawer",
    }


async def test_conflicting_rules_wait_in_the_inbox_until_the_owner_decides(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme, beta = await r.contact("ACME"), await r.contact("Beta")
    await r.user_rule(owner, definition("A", channel_api(), SetContact(acme.id), priority=200))
    await r.user_rule(owner, definition("B", channel_api(), SetContact(beta.id)))

    document = await r.arrive(owner)

    assert document.lane is Lane.YELLOW
    assert document.contact_id is None  # priority orders, but never decides
    (item,) = await r.documents.inbox(owner.id)
    (open,) = item.open
    assert open.step is Step.APPLY_RULES
    (check,) = open.fields
    assert (check.field, check.suggestion) == ("contact", str(acme.id))
    assert ("contact", "conflict") in notes(
        (await r.rule_entries(document, RULES))[0].result.output
    )

    await r.pipeline().confirm(owner.id, document.id, DocumentChanges(), accept_suggestions=True)
    await world.drain(r.pipeline())

    document = await r.stored(document)
    assert (document.lane, document.contact_id) == (Lane.GREEN, acme.id)
    # The rules ran again on the decided field: still in conflict, but nothing is open.
    again = (await r.rule_entries(document, RULES))[-1]
    assert again.result.outcome is Outcome.OK
    assert notes(again.result.output) == [("contact", "conflict"), ("contact", "conflict")]
    assert again.result.output["fields"] == []


async def test_a_rule_does_not_replace_the_models_value(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    model, rule = await r.contact("Model"), await r.contact("Rule")
    await r.user_rule(owner, definition("Rule", channel_api(), SetContact(rule.id)))

    document = await r.arrive(owner, classify=Classifies(contact=model.id))

    assert (document.lane, document.contact_id) == (Lane.YELLOW, model.id)
    ((check,),) = [open.fields for open in (await r.documents.review(owner.id, document.id)).open]
    assert check.field == "contact"
    assert check.reason == f"rules: the model set another value ({model.id})"
    assert check.suggestion == str(rule.id)

    # The owner takes the rule's value; it is not yellow again.
    await r.pipeline().confirm(owner.id, document.id, DocumentChanges(contact_id=rule.id))
    await world.drain(r.pipeline())
    document = await r.stored(document)
    assert (document.lane, document.contact_id) == (Lane.GREEN, rule.id)


async def test_a_forced_review_holds_the_document_once(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    await r.user_rule(owner, definition("Mine", channel_api(), ForceReview("look at it")))
    await r.global_rule(definition("Everyone's", channel_api(), ForceReview("audit")))

    document = await r.arrive(owner)

    assert document.lane is Lane.YELLOW
    (entry,) = await r.rule_entries(document, RULES)
    assert entry.result.reason is not None
    assert "rule 'Mine': look at it" in entry.result.reason
    assert "rule 'Everyone's': audit" in entry.result.reason

    await r.pipeline().confirm(owner.id, document.id, DocumentChanges())
    await world.drain(r.pipeline())

    document = await r.stored(document)
    assert document.lane is Lane.GREEN
    again = (await r.rule_entries(document, RULES))[-1]
    assert notes(again.result.output) == [("review", "overruled"), ("review", "overruled")]
    confirmed = [entry for entry in await r.log(document) if entry.result.model_version == PERSON]
    assert [entry.step for entry in confirmed] == [Step.APPLY_RULES]


async def test_unconfirmed_model_values_do_not_file_into_a_drawer_others_see(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, reader = await world.user(), await world.user()
    acme = await r.contact("ACME")
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    await r.user_rule(owner, definition("ACME", contact_is(acme.id), SetDrawer(shared.id)))

    document = await r.arrive(owner, classify=Classifies(contact=acme.id))

    assert document.lane is Lane.YELLOW
    assert document.drawer_id == (await world.default_drawer(owner)).id
    (open,) = (await r.documents.review(owner.id, document.id)).open
    assert [(check.field, check.suggestion) for check in open.fields] == [("contact", str(acme.id))]
    (entry,) = await r.rule_entries(document, RULES)
    assert notes(entry.result.output) == [("drawer", "refused")]
    assert await world.documents.list_visible(reader.id) == []

    # Confirmed by the owner, the contact is trusted: the rule files.
    await r.pipeline().confirm(owner.id, document.id, DocumentChanges())
    await world.drain(r.pipeline())
    document = await r.stored(document)
    assert (document.lane, document.drawer_id) == (Lane.GREEN, shared.id)
    assert [item.id for item in await world.documents.list_visible(reader.id)] == [document.id]


async def test_unconfirmed_model_values_still_file_into_a_private_drawer(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    private = await world.drawers.create(owner.id, "Private")
    await r.user_rule(owner, definition("ACME", contact_is(acme.id), SetDrawer(private.id)))

    document = await r.arrive(owner, classify=Classifies(contact=acme.id))

    assert (document.lane, document.drawer_id) == (Lane.GREEN, private.id)


async def test_unconfirmed_model_tags_do_not_file_into_another_users_drawer(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    tax = await r.tag("tax")
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    await r.user_rule(owner, definition("Tax", has_tag(tax.id), SetDrawer(office.id)))

    document = await r.arrive(owner, classify=Classifies(tags=[tax.id]))

    assert document.lane is Lane.YELLOW
    assert document.drawer_id == (await world.default_drawer(owner)).id
    (open,) = (await r.documents.review(owner.id, document.id)).open
    assert [check.field for check in open.fields] == ["tags"]


async def test_a_withdrawn_share_stops_the_rule_from_filing(world: World) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    await r.user_rule(owner, definition("Office", channel_api(), SetDrawer(office.id)))
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ)

    document = await r.arrive(owner)

    default = await world.default_drawer(owner)
    assert (document.lane, document.drawer_id) == (Lane.YELLOW, default.id)
    (open,) = (await r.documents.review(owner.id, document.id)).open
    (check,) = open.fields
    assert check.field == "drawer"
    assert check.reason == f"rule 'Office': no write access to drawer {office.id} (any more)"

    # Keeping the drawer: the rule is refused again, but the document is filed.
    await r.pipeline().confirm(owner.id, document.id, DocumentChanges())
    await world.drain(r.pipeline())
    document = await r.stored(document)
    assert (document.lane, document.drawer_id) == (Lane.GREEN, default.id)


async def test_filing_checks_the_owners_write_access_once_more(world: World) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    pipeline = r.pipeline()
    received = await pipeline.receive(owner.id, incoming_pdf(), filename="a.pdf", drawer=office.id)
    await world.drawers.unshare(colleague.id, office.id, owner.id)

    await world.drain(pipeline)

    document = await r.stored(received)
    assert document.lane is Lane.YELLOW
    assert document.processing.outcomes[Step.FILE] is Outcome.UNCERTAIN
    assert "document.filed" not in world.event_types()
    (open,) = (await r.documents.review(owner.id, document.id)).open
    assert (open.step, [check.field for check in open.fields]) == (Step.FILE, ["drawer"])

    # Keeping the drawer would stop filing again: the owner has to choose one.
    with pytest.raises(OpenFieldsError) as error:
        await pipeline.confirm(owner.id, document.id, DocumentChanges())
    assert error.value.fields == ("drawer",)

    default = await world.default_drawer(owner)
    await pipeline.confirm(owner.id, document.id, DocumentChanges(), drawer=default.id)
    await world.drain(pipeline)
    document = await r.stored(document)
    assert (document.lane, document.drawer_id) == (Lane.GREEN, default.id)


async def test_the_rules_of_a_deactivated_owner_do_not_run(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    mine, everyone = await r.tag("mine"), await r.tag("everyone")
    await r.user_rule(owner, definition("Mine", channel_api(), add_tags(mine.id)))
    shared = await r.global_rule(definition("All", channel_api(), add_tags(everyone.id)))
    pipeline = r.pipeline()
    received = await pipeline.receive(owner.id, incoming_pdf(), filename="a.pdf")
    await r.deactivate(owner)

    await world.drain(pipeline)

    document = await r.stored(received)
    assert document.tag_ids == {everyone.id}
    (entry,) = await r.rule_entries(document, RULES)
    assert entry.result.input["rules"] == [{"id": str(shared.id), "version": 1}]


async def test_a_global_rule_acts_on_every_document_but_never_moves_it(world: World) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    seen = await r.tag("seen")
    await r.global_rule(definition("Seen", channel_api(), add_tags(seen.id)))

    mine = await r.arrive(owner)
    theirs = await r.arrive(colleague)
    filed = await r.arrive(owner, drawer=office.id)

    for document, drawer in (
        (mine, (await world.default_drawer(owner)).id),
        (theirs, (await world.default_drawer(colleague)).id),
        (filed, office.id),
    ):
        assert (document.lane, document.drawer_id, document.tag_ids) == (
            Lane.GREEN,
            drawer,
            {seen.id},
        )


async def test_a_pattern_that_times_out_does_not_match(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    r = await rule_world(world)
    owner = await world.user()
    slow, bill = await r.tag("slow"), await r.tag("bill")
    r.matcher.slow.add("(a+)+$")
    await r.user_rule(owner, definition("Slow", text_matches("(a+)+$"), add_tags(slow.id)))
    await r.user_rule(owner, definition("Bill", text_matches(r"Rech\w+"), add_tags(bill.id)))

    with caplog.at_level(logging.WARNING):
        document = await r.arrive(owner, text="# Rechnung\n\naaaaaaaaaaaaaaaaaaaaaaaab")

    assert document.processing.status is ProcessingStatus.COMPLETED
    assert (document.lane, document.tag_ids) == (Lane.GREEN, {bill.id})
    (entry,) = await r.rule_entries(document, RULES)
    assert entry.result.output["problems"] == ["pattern '(a+)+$' took longer than 0.2 s"]
    assert "rule pattern skipped" in caplog.messages


async def test_rules_do_not_set_each_other_off_on_arrival(world: World) -> None:
    """Conditions see the state before any rule acts: a tag added by one rule does not make
    another rule match in the same run."""
    r = await rule_world(world)
    owner = await world.user()
    first, second = await r.tag("first"), await r.tag("second")
    await r.user_rule(owner, definition("First", channel_api(), add_tags(first.id)))
    await r.user_rule(owner, definition("Second", has_tag(first.id), add_tags(second.id)))

    document = await r.arrive(owner)

    assert document.tag_ids == {first.id}


async def test_patterns_stop_when_their_time_is_used_up(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    rule = await r.user_rule(
        owner, definition("Bill", text_matches("Rech"), SetTitle("Bill"), triggers=CHANGE)
    )
    document = await r.arrive(owner, text="# Rechnung")

    prepared = await prepare(
        world.object_store,
        r.matcher,
        [rule],
        document,
        max_text=1000,
        budget=PatternBudget(0),
    )

    assert prepared.patterns == {}
    assert prepared.problems == ("pattern 'Rech' skipped: the time for patterns is used up",)
    assert r.matcher.searched == []

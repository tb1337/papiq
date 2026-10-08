"""The person wins: what a person decided earlier (a drawer chosen on upload or by moving, tags,
a confirmed review) is not undone by rules later in the processing run."""

from papiq.core.domain.classification import FieldCheck, checks_to_json
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.pipeline import Lane, Outcome, StepResult
from papiq.core.domain.rule_engine import Note
from papiq.core.domain.rules import ForceReview, RemoveTags, SetDrawer, Trigger
from papiq.core.services.inbox import PERSON_DRAWER, RULES
from papiq.core.services.pipeline import MetadataResult
from tests.unit.services.conftest import World
from tests.unit.services.rules_support import (
    channel_api,
    contact_is,
    definition,
    incoming_pdf,
    notes,
    rule_world,
)

CHANGE = (Trigger.CHANGE,)
INGEST = (Trigger.INGEST,)


class UnsureOfTheContact:
    """Classification that leaves the contact open: the document waits in the inbox."""

    async def run(self, document: Document) -> MetadataResult:
        check = FieldCheck(
            field="contact", outcome=Outcome.UNCERTAIN, confidence=0.2, reason="unsure"
        )
        result = StepResult(
            outcome=Outcome.UNCERTAIN,
            reason="contact: unsure",
            model_version="fake-llm 1",
            output={"fields": checks_to_json([check])},
        )
        return MetadataResult(result, DocumentChanges())


async def test_a_drawer_chosen_on_upload_stays(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    chosen = await world.drawers.create(owner.id, "Chosen")
    other = await world.drawers.create(owner.id, "Other")
    await r.user_rule(
        owner, definition("Other", channel_api(), SetDrawer(other.id), triggers=INGEST)
    )

    document = await r.arrive(owner, drawer=chosen.id)

    assert (document.lane, document.drawer_id) == (Lane.GREEN, chosen.id)
    (entry,) = await r.rule_entries(document, RULES)
    assert notes(entry.result.output) == [("drawer", "overruled")]
    (choice,) = [e for e in await r.log(document) if e.result.model_version == PERSON_DRAWER]
    assert choice.result.output["changed"] == ["drawer"]


async def test_a_change_rule_leaves_the_drawer_the_owner_moved_the_document_into(
    world: World,
) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    mine = await world.drawers.create(owner.id, "Mine")
    rules = await world.drawers.create(owner.id, "Rules")
    await r.user_rule(
        owner, definition("ACME", contact_is(acme.id), SetDrawer(rules.id), triggers=CHANGE)
    )
    document = await r.arrive(owner)
    await r.documents.move(owner.id, document.id, mine.id)

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert change.rules is not None
    assert [note for report in change.rules.plan.reports for note in report.notes] == [
        Note("drawer", "overruled", "a person decided this field")
    ]
    assert (await r.stored(document)).drawer_id == mine.id


async def test_a_change_rule_keeps_a_tag_the_person_added_earlier(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    keep = await r.tag("keep")
    await r.user_rule(
        owner,
        definition("ACME", contact_is(acme.id), RemoveTags(frozenset({keep.id})), triggers=CHANGE),
    )
    document = await r.arrive(owner)
    await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(tag_ids=frozenset({keep.id}))
    )

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert change.rules is not None
    kinds = [(n.field, n.kind) for report in change.rules.plan.reports for n in report.notes]
    assert kinds == [("tags", "overruled")]
    assert (await r.stored(document)).tag_ids == {keep.id}


async def test_a_review_the_confirmation_sets_off_still_holds_the_document(
    world: World,
) -> None:
    """Confirming answers the reviews the owner saw, not one that a value they entered sets
    off."""
    r = await rule_world(world)
    owner = await world.user()
    acme = await r.contact("ACME")
    await r.user_rule(owner, definition("ACME", contact_is(acme.id), ForceReview("check")))
    pipeline = r.pipeline(UnsureOfTheContact())
    received = await pipeline.receive(owner.id, incoming_pdf(), filename="a.pdf")
    await world.drain(pipeline)
    assert (await r.stored(received)).lane is Lane.YELLOW

    await pipeline.confirm(owner.id, received.id, DocumentChanges(contact_id=acme.id))
    await world.drain(pipeline)

    document = await r.stored(received)
    assert document.lane is Lane.YELLOW
    assert notes((await r.rule_entries(document, RULES))[-1].result.output) == [
        ("review", "review")
    ]

    await pipeline.confirm(owner.id, document.id, DocumentChanges())
    await world.drain(pipeline)
    document = await r.stored(document)
    assert document.lane is Lane.GREEN
    assert notes((await r.rule_entries(document, RULES))[-1].result.output) == [
        ("review", "overruled")
    ]


async def test_a_change_rule_into_a_drawer_no_longer_shared_is_only_reported(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    acme = await r.contact("ACME")
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    await r.user_rule(
        owner, definition("Office", contact_is(acme.id), SetDrawer(office.id), triggers=CHANGE)
    )
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ)
    document = await r.arrive(owner)

    change = await r.documents.change_metadata(
        owner.id, document.id, DocumentChanges(contact_id=acme.id)
    )

    assert change.rules is not None
    kinds = [(n.field, n.kind) for report in change.rules.plan.reports for n in report.notes]
    assert kinds == [("drawer", "refused")]
    stored = await r.stored(document)
    assert (stored.lane, stored.drawer_id) == (Lane.GREEN, document.drawer_id)

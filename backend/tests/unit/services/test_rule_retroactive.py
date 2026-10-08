"""Applying a rule to existing documents: the preview and the background job."""

import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from papiq.core.domain.ids import DocumentId, new_id
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.rules import ApplicationStatus, ForceReview, SetTitle
from papiq.core.services.inbox import RULES_APPLY
from papiq.core.services.rules.retroactive import BATCH
from tests.unit.services.conftest import World
from tests.unit.services.rules_support import (
    MAX_DOCUMENTS,
    RuleWorld,
    add_tags,
    channel_api,
    definition,
    incoming_pdf,
    rule_world,
)


async def run_jobs(r: RuleWorld) -> int:
    count = 0
    while await r.applications.run_next_job():
        count += 1
    return count


async def test_the_preview_lists_changes_and_the_job_applies_the_pinned_version(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, other = await world.user(), await world.user()
    tax = await r.tag("tax")
    first = await r.arrive(owner, filename="First.pdf")
    second = await r.arrive(owner, filename="Second.pdf")
    third = await r.arrive(owner, filename="Third.pdf")
    for document, title in ((first, "Mine 1"), (second, "Mine 2")):
        await r.documents.change_metadata(owner.id, document.id, DocumentChanges(title=title))
    await r.arrive(other)
    await r.pipeline().receive(owner.id, incoming_pdf(), filename="Busy.pdf")  # processing
    rule = await r.user_rule(
        owner,
        definition(
            "Tax",
            channel_api(),
            add_tags(tax.id),
            SetTitle("Tax {filename}"),
            ForceReview("check"),
        ),
    )

    preview = await r.applications.preview(owner.id, rule.id)

    assert preview.next_cursor is None
    items = {item.document.id: item for item in preview.items}
    assert set(items) == {first.id, second.id, third.id}
    assert [effect.field for effect in items[first.id].effects] == ["tags"]
    assert [conflict.field for conflict in items[first.id].conflicts] == ["title"]
    assert [(note.field, note.kind) for note in items[first.id].notes] == [("review", "skipped")]
    assert [effect.field for effect in items[third.id].effects] == ["tags", "title"]
    assert items[third.id].conflicts == ()

    application = await r.applications.start(
        owner.id,
        rule.id,
        version=1,
        documents=[first.id, second.id, third.id],
        accept_conflicts=[first.id],
    )
    # A new version meanwhile does not change what the application does.
    await r.rules.change(
        owner.id, rule.id, definition("Tax v2", channel_api(), SetTitle("V2 {filename}"))
    )
    assert await run_jobs(r) == 1

    done = await r.applications.get(owner.id, application.id)
    assert (done.status, done.applied, done.unchanged, done.skipped) == (
        ApplicationStatus.DONE,
        3,
        0,
        [],
    )
    titles = [(await r.stored(document)).title for document in (first, second, third)]
    assert titles == ["Tax First", "Mine 2", "Tax Third"]  # the conflict only where accepted
    for document in (first, second, third):
        stored = await r.stored(document)
        assert (stored.tag_ids, stored.lane) == ({tax.id}, Lane.GREEN)  # no forced review
    (entry,) = await r.rule_entries(first, RULES_APPLY)
    assert entry.result.input == {
        "trigger": "apply",
        "actor": str(owner.id),
        "application_id": str(application.id),
        "rules": [{"id": str(rule.id), "version": 1}],
    }


async def test_the_job_checks_the_rights_again_and_a_global_rule_moves_nothing(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, editor = await world.user(), await world.user()
    seen = await r.tag("seen")
    shared = await world.drawers.create(owner.id, "Shared")
    readable = await world.drawers.create(owner.id, "Readable")
    await world.drawers.share(owner.id, shared.id, editor.id, ShareLevel.READ_WRITE)
    await world.drawers.share(owner.id, readable.id, editor.id, ShareLevel.READ)
    in_shared = await r.arrive(owner, drawer=shared.id)
    await r.arrive(owner, drawer=readable.id)
    private = await r.arrive(owner)
    own = await r.arrive(editor)
    rule = await r.global_rule(
        definition("Audit", channel_api(), add_tags(seen.id), ForceReview("audit"))
    )

    # A global rule: by anyone, on the documents they may write to.
    preview = await r.applications.preview(editor.id, rule.id)
    assert {item.document.id for item in preview.items} == {in_shared.id, own.id}

    application = await r.applications.start(
        editor.id, rule.id, version=1, documents=[in_shared.id, own.id, private.id]
    )
    await world.drawers.share(owner.id, shared.id, editor.id, ShareLevel.READ)
    await run_jobs(r)

    done = await r.applications.get(editor.id, application.id)
    assert (done.status, done.applied) == (ApplicationStatus.DONE, 1)
    assert done.skipped == [(in_shared.id, "not available"), (private.id, "not available")]
    assert (await r.stored(in_shared)).tag_ids == set()
    assert (await r.stored(private)).tag_ids == set()
    changed = await r.stored(own)
    assert (changed.tag_ids, changed.lane, changed.drawer_id) == (
        {seen.id},
        Lane.GREEN,
        own.drawer_id,
    )


async def test_a_user_rule_is_applied_by_its_owner_to_their_own_documents(world: World) -> None:
    r = await rule_world(world)
    owner, colleague = await world.user(), await world.user()
    tax = await r.tag("tax")
    office = await world.drawers.create(colleague.id, "Office")
    await world.drawers.share(colleague.id, office.id, owner.id, ShareLevel.READ_WRITE)
    theirs = await r.arrive(colleague, drawer=office.id)
    mine = await r.arrive(owner)
    rule = await r.user_rule(owner, definition("Tax", channel_api(), add_tags(tax.id)))

    preview = await r.applications.preview(owner.id, rule.id)
    assert [item.document.id for item in preview.items] == [mine.id]
    application = await r.applications.start(
        owner.id, rule.id, version=1, documents=[theirs.id, mine.id]
    )
    await run_jobs(r)

    done = await r.applications.get(owner.id, application.id)
    assert (done.applied, done.skipped) == (1, [(theirs.id, "not available")])
    assert (await r.stored(theirs)).tag_ids == set()

    # Nobody else applies it or reads the application, admins included.
    with pytest.raises(NotFoundError):
        await r.applications.preview(colleague.id, rule.id)
    with pytest.raises(PermissionDeniedError):
        await r.applications.preview(r.admin.id, rule.id)
    with pytest.raises(PermissionDeniedError):
        await r.applications.start(r.admin.id, rule.id, version=1, documents=[mine.id])
    for stranger in (colleague, r.admin):
        with pytest.raises(NotFoundError):
            await r.applications.get(stranger.id, application.id)

    with pytest.raises(NotFoundError):
        await r.applications.start(owner.id, rule.id, version=2, documents=[mine.id])
    too_many = [DocumentId(new_id()) for _ in range(MAX_DOCUMENTS + 1)]
    with pytest.raises(ValidationError):
        await r.applications.start(owner.id, rule.id, version=1, documents=too_many)


async def test_a_long_application_continues_in_a_new_job(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    tax = await r.tag("tax")
    documents = [await r.arrive(owner) for _ in range(BATCH + 1)]
    rule = await r.user_rule(owner, definition("Tax", channel_api(), add_tags(tax.id)))
    application = await r.applications.start(
        owner.id, rule.id, version=1, documents=[document.id for document in documents]
    )

    assert await r.applications.run_next_job()
    progress = await r.applications.get(owner.id, application.id)
    assert (progress.status, progress.position, progress.finished_at) == (
        ApplicationStatus.RUNNING,
        BATCH,
        None,
    )

    assert await run_jobs(r) == 1
    done = await r.applications.get(owner.id, application.id)
    assert (done.status, done.applied) == (ApplicationStatus.DONE, BATCH + 1)
    for document in documents:
        assert (await r.stored(document)).tag_ids == {tax.id}


async def test_an_application_ends_when_its_rule_is_deleted(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    tax = await r.tag("tax")
    document = await r.arrive(owner)
    rule = await r.user_rule(owner, definition("Tax", channel_api(), add_tags(tax.id)))
    application = await r.applications.start(owner.id, rule.id, version=1, documents=[document.id])
    await r.rules.delete(owner.id, rule.id)

    await run_jobs(r)

    done = await r.applications.get(owner.id, application.id)
    assert (done.status, done.error) == (ApplicationStatus.FAILED, "the rule or its user is gone")
    assert (await r.stored(document)).tag_ids == set()
    with pytest.raises(NotFoundError):
        await r.applications.preview(owner.id, rule.id)

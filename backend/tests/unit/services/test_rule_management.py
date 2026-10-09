"""RuleService: who may read and change which rules, references, deleting rules, and rules whose
references go away."""

import pytest

from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from papiq.core.domain.fields import FieldType
from papiq.core.domain.ids import ContactId, DrawerId, new_id
from papiq.core.domain.rules import (
    AddTags,
    ForceReview,
    Rule,
    RuleScope,
    SetContact,
    SetDocumentType,
    SetDrawer,
    SetField,
    SetTitle,
)
from tests.unit.services.conftest import World
from tests.unit.services.rules_support import channel_api, definition, rule_world


async def test_user_rules_are_their_owners_and_global_rules_the_admins(world: World) -> None:
    r = await rule_world(world)
    owner, other = await world.user(), await world.user()
    title = definition("Title", channel_api(), SetTitle("Scan"))
    mine = await r.user_rule(owner, title)
    theirs = await r.user_rule(other, title)
    review = definition("Check", channel_api(), ForceReview("check"))

    # Only admins make and change global rules; everyone reads them.
    with pytest.raises(PermissionDeniedError):
        await r.rules.create(owner.id, RuleScope.GLOBAL, review)
    shared = await r.global_rule(review)
    assert shared.owner_id is None
    assert (await r.rules.get(owner.id, shared.id)).id == shared.id
    with pytest.raises(PermissionDeniedError):
        await r.rules.change(owner.id, shared.id, review)
    with pytest.raises(PermissionDeniedError):
        await r.rules.set_enabled(owner.id, shared.id, False)
    with pytest.raises(PermissionDeniedError):
        await r.rules.delete(owner.id, shared.id)
    assert (await r.rules.change(r.admin.id, shared.id, review)).current.number == 2

    # Another user does not learn that a user rule exists.
    for action in (
        r.rules.get(other.id, mine.id),
        r.rules.versions(other.id, mine.id),
        r.rules.version(other.id, mine.id, 1),
        r.rules.change(other.id, mine.id, title),
        r.rules.set_enabled(other.id, mine.id, False),
        r.rules.delete(other.id, mine.id),
    ):
        with pytest.raises(NotFoundError):
            await action

    # Admins read and change every rule (Tobi, 08.10.2026); the owner stays.
    assert (await r.rules.get(r.admin.id, mine.id)).id == mine.id
    assert len(await r.rules.versions(r.admin.id, mine.id)) == 1
    changed = await r.rules.change(r.admin.id, mine.id, title)
    assert (changed.owner_id, changed.current.number) == (owner.id, 2)
    assert changed.current.created_by == r.admin.id
    assert not (await r.rules.set_enabled(r.admin.id, mine.id, False)).enabled
    assert (await r.rules.set_enabled(r.admin.id, mine.id, True)).enabled

    # Listing: the caller's rules and the global ones; all users' for admins only.
    assert {rule.id for rule in await r.rules.list(owner.id)} == {mine.id, shared.id}
    assert {rule.id for rule in await r.rules.list(owner.id, scope=RuleScope.USER)} == {mine.id}
    assert {rule.id for rule in await r.rules.list(r.admin.id)} == {shared.id}
    with pytest.raises(PermissionDeniedError):
        await r.rules.list(owner.id, all_users=True)
    everything = await r.rules.list(r.admin.id, all_users=True)
    assert {rule.id for rule in everything} == {mine.id, theirs.id, shared.id}
    assert everything[0].id == shared.id  # same priority: global rules first


async def test_rules_refer_only_to_what_exists_and_file_where_the_owner_may(world: World) -> None:
    r = await rule_world(world)
    owner, other = await world.user(), await world.user()
    foreign = await world.drawers.create(other.id, "Office")

    async def files_into(drawer: DrawerId) -> Rule:
        return await r.rules.create(
            owner.id, RuleScope.USER, definition("File", channel_api(), SetDrawer(drawer))
        )

    with pytest.raises(NotFoundError):
        await files_into(foreign.id)  # not shared: does not exist for the owner
    with pytest.raises(NotFoundError):
        await files_into(DrawerId(new_id()))
    await world.drawers.share(other.id, foreign.id, owner.id, ShareLevel.READ)
    with pytest.raises(PermissionDeniedError):
        await files_into(foreign.id)
    await world.drawers.share(other.id, foreign.id, owner.id, ShareLevel.READ_WRITE)
    rule = await r.user_rule(owner, definition("File", channel_api(), SetDrawer(foreign.id)))

    # Changing and enabling check again.
    await world.drawers.share(other.id, foreign.id, owner.id, ShareLevel.READ)
    await r.rules.set_enabled(owner.id, rule.id, False)
    with pytest.raises(PermissionDeniedError):
        await r.rules.set_enabled(owner.id, rule.id, True)
    with pytest.raises(PermissionDeniedError):
        await r.rules.change(
            owner.id, rule.id, definition("File again", channel_api(), SetDrawer(foreign.id))
        )
    assert (await r.rules.get(owner.id, rule.id)).current.number == 1

    with pytest.raises(NotFoundError):
        await r.user_rule(owner, definition("C", channel_api(), SetContact(ContactId(new_id()))))


async def test_a_deleted_rule_never_runs_and_its_versions_stay_readable(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    tag = await r.tag("tax")
    rule = await r.user_rule(owner, definition("Tax", channel_api(), AddTags(frozenset({tag.id}))))
    await r.rules.change(
        owner.id, rule.id, definition("Tax v2", channel_api(), AddTags(frozenset({tag.id})))
    )
    await r.rules.delete(owner.id, rule.id)

    with pytest.raises(NotFoundError):
        await r.rules.get(owner.id, rule.id)
    with pytest.raises(NotFoundError):
        await r.rules.set_enabled(owner.id, rule.id, True)
    with pytest.raises(NotFoundError):
        await r.rules.delete(owner.id, rule.id)
    assert await r.rules.list(owner.id) == []
    assert await r.rules.list(r.admin.id, all_users=True) == []
    versions = await r.rules.versions(owner.id, rule.id)
    assert [version.definition.name for version in versions] == ["Tax", "Tax v2"]

    document = await r.arrive(owner)
    assert document.tag_ids == set()
    (entry,) = await r.rule_entries(document, "rules")
    assert entry.result.input["rules"] == []


async def test_deleting_master_data_and_drawers_disables_the_rules_that_use_them(
    world: World,
) -> None:
    r = await rule_world(world)
    owner, sharer = await world.user(), await world.user()
    contact = await r.contact("ACME")
    kind = await world.master_data.create_document_type(r.admin.id, "Invoice")
    tag = await r.tag("tax")
    drawer = await world.drawers.create(sharer.id, "Shared")
    await world.drawers.share(sharer.id, drawer.id, owner.id, ShareLevel.READ_WRITE)
    by_contact = await r.user_rule(owner, definition("C", channel_api(), SetContact(contact.id)))
    by_type = await r.user_rule(owner, definition("T", channel_api(), SetDocumentType(kind.id)))
    by_tag = await r.global_rule(definition("G", channel_api(), AddTags(frozenset({tag.id}))))
    by_drawer = await r.user_rule(owner, definition("D", channel_api(), SetDrawer(drawer.id)))
    note = await world.master_data.create_field(r.admin.id, "Note", FieldType.TEXT)
    by_field = await r.user_rule(
        owner, definition("A", channel_api(), SetField(note.id, "from a rule"))
    )
    unrelated = await r.user_rule(owner, definition("U", channel_api(), SetTitle("Scan")))

    await world.master_data.delete_contact(r.admin.id, contact.id)
    await world.master_data.delete_document_type(r.admin.id, kind.id)
    await world.master_data.delete_tag(r.admin.id, tag.id)
    await world.drawers.delete(sharer.id, drawer.id)
    await world.master_data.delete_field(r.admin.id, note.id)

    expected = {
        by_contact.id: "contact 'ACME' was deleted",
        by_type.id: "document type 'Invoice' was deleted",
        by_tag.id: "tag 'tax' was deleted",
        by_drawer.id: "drawer 'Shared' was deleted",
        by_field.id: "field 'Note' was deleted",
    }
    for id, reason in expected.items():
        rule = await r.rules.get(r.admin.id, id)
        assert (rule.enabled, rule.disabled_reason) == (False, reason)
        # Enabling checks the references again.
        actor = r.admin if rule.scope is RuleScope.GLOBAL else owner
        with pytest.raises(NotFoundError):
            await r.rules.set_enabled(actor.id, id, True)
    assert (await r.rules.get(owner.id, unrelated.id)).enabled


async def test_removing_a_choice_a_rule_uses_disables_the_rule(world: World) -> None:
    r = await rule_world(world)
    owner = await world.user()
    size = await world.master_data.create_field(
        r.admin.id, "Size", FieldType.CHOICE, choices=["S", "M"]
    )
    small = await r.user_rule(owner, definition("S", channel_api(), SetField(size.id, "S")))
    medium = await r.user_rule(owner, definition("M", channel_api(), SetField(size.id, "M")))

    await world.master_data.change_field(r.admin.id, size.id, choices=["S"])
    disabled = await r.rules.get(owner.id, medium.id)
    assert disabled.enabled is False
    assert disabled.disabled_reason == "field 'Size' no longer allows a value the rule uses"
    assert (await r.rules.get(owner.id, small.id)).enabled
    with pytest.raises(ValidationError):
        await r.rules.set_enabled(owner.id, medium.id, True)

    await world.master_data.change_field(r.admin.id, size.id, choices=["S", "M"])
    enabled = await r.rules.set_enabled(owner.id, medium.id, True)
    assert (enabled.enabled, enabled.disabled_reason) == (True, None)


async def test_deleting_a_user_removes_their_rules(world: World) -> None:
    r = await rule_world(world)
    leaving, staying = await world.user(), await world.user()
    drawer = await world.drawers.create(leaving.id, "Shared")
    await world.drawers.share(leaving.id, drawer.id, staying.id, ShareLevel.READ_WRITE)
    gone = await r.user_rule(leaving, definition("Mine", channel_api(), SetTitle("Scan")))
    files = await r.user_rule(staying, definition("File", channel_api(), SetDrawer(drawer.id)))

    await world.users.delete_user(r.admin.id, leaving.id)

    with pytest.raises(NotFoundError):
        await r.rules.get(r.admin.id, gone.id)
    assert [rule.id for rule in await r.rules.list(r.admin.id, all_users=True)] == [files.id]
    rule = await r.rules.get(staying.id, files.id)
    assert (rule.enabled, rule.disabled_reason) == (False, "drawer 'Shared' was deleted")

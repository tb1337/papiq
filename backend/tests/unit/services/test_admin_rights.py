"""Admins have every right and see everything (Tobi, 08.10.2026); other users gain nothing from
it, and a deactivated admin has no rights. Lists show the caller's reach unless an admin asks
for all users."""

import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.domain.rules import RuleScope, SetDrawer
from papiq.core.domain.users import Role, User
from papiq.core.ports import DocumentFilter
from tests.builders import UNCERTAIN, incoming
from tests.unit.services.conftest import Returns, World
from tests.unit.services.rules_support import add_tags, channel_api, definition, rule_world


async def _second_admin(world: World) -> tuple[User, User]:
    """An admin, and another one who can deactivate the first."""
    return await world.user("admin", role=Role.ADMIN), await world.user("boss", role=Role.ADMIN)


async def test_admins_list_drawers_of_everyone_and_manage_them(world: World) -> None:
    admin, boss = await _second_admin(world)
    owner, friend, stranger = await world.user(), await world.user(), await world.user()
    office = await world.drawers.create(owner.id, "Office")
    await world.drawers.share(owner.id, office.id, friend.id, ShareLevel.READ)

    assert office.id in {drawer.id for drawer in await world.drawers.list(admin.id)}
    assert office.id not in {drawer.id for drawer in await world.drawers.list(stranger.id)}
    assert (await world.drawers.get(admin.id, office.id)).id == office.id

    renamed = await world.drawers.rename(admin.id, office.id, "Büro")
    assert (renamed.name, renamed.owner_id) == ("Büro", owner.id)
    shared = await world.drawers.share(admin.id, office.id, stranger.id, ShareLevel.READ_WRITE)
    assert shared.shares[stranger.id] is ShareLevel.READ_WRITE
    await world.drawers.unshare(admin.id, office.id, stranger.id)
    # The default drawer stays private and cannot be deleted, by admins neither.
    default = await world.default_drawer(owner)
    with pytest.raises(ConflictError):
        await world.drawers.delete(admin.id, default.id)

    # Nobody else gains anything.
    for user in (friend, stranger):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await world.drawers.rename(user.id, office.id, "Mine")

    await world.drawers.delete(admin.id, office.id)
    with pytest.raises(NotFoundError):
        await world.drawers.get(owner.id, office.id)

    # A deactivated admin has no rights.
    other = await world.drawers.create(owner.id, "Other")
    await world.users.set_active(boss.id, admin.id, False)
    with pytest.raises(AuthenticationError):
        await world.drawers.rename(admin.id, other.id, "Gone")
    with pytest.raises(AuthenticationError):
        await world.drawers.list(admin.id)


async def test_admins_control_other_users_documents_in_every_lane(world: World) -> None:
    admin, boss = await _second_admin(world)
    owner, stranger = await world.user(), await world.user()
    pipeline = world.pipeline()
    received = await pipeline.receive(owner.id, incoming(b"%PDF-1.7 a"), filename="a.pdf")
    # In processing: readable and controllable by the admin only (besides the owner).
    assert (await world.documents.get(admin.id, received.id)).lane is None
    with pytest.raises(NotFoundError):
        await world.documents.get(stranger.id, received.id)
    await world.drain(world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)}))
    yellow = await world.documents.get(admin.id, received.id)
    assert yellow.lane is Lane.YELLOW

    assert await world.documents.processing_log(admin.id, yellow.id)
    assert (await world.documents.review(admin.id, yellow.id)).document.id == yellow.id
    changed = await world.documents.update_metadata(
        admin.id, yellow.id, DocumentChanges(title="By the admin")
    )
    assert changed.title == "By the admin"

    # The owner's inbox holds it; the admin's own inbox not, unless they ask for all users.
    assert [item.document.id for item in await world.documents.inbox(owner.id)] == [yellow.id]
    assert await world.documents.inbox(admin.id) == []
    every = await world.documents.inbox(admin.id, all_users=True)
    assert [item.document.id for item in every] == [yellow.id]
    listed = await world.documents.query(admin.id, _all(), all_users=True)
    assert [view.document.id for view in listed] == [yellow.id]
    assert await world.documents.query(admin.id, _all()) == []
    for user in (owner, stranger):
        with pytest.raises(PermissionDeniedError):
            await world.documents.inbox(user.id, all_users=True)
        with pytest.raises(PermissionDeniedError):
            await world.documents.query(user.id, _all(), all_users=True)
    with pytest.raises(NotFoundError):
        await world.documents.processing_log(stranger.id, yellow.id)

    owners_drawer = await world.drawers.create(owner.id, "Archive")
    confirmed = await pipeline.confirm(
        admin.id,
        yellow.id,
        DocumentChanges(),
        accept_suggestions=True,
        drawer=owners_drawer.id,
    )
    assert confirmed.drawer_id == owners_drawer.id
    await world.drain()
    assert (await world.documents.get(owner.id, yellow.id)).lane is Lane.GREEN

    await world.pipeline().reprocess_from(admin.id, yellow.id, Step.PARSE)
    await world.drain()
    await world.users.set_active(boss.id, admin.id, False)
    with pytest.raises(AuthenticationError):
        await world.documents.get(admin.id, yellow.id)
    with pytest.raises(AuthenticationError):
        await world.documents.delete(admin.id, yellow.id)
    await world.users.set_active(boss.id, admin.id, True)
    await world.documents.delete(admin.id, yellow.id)
    with pytest.raises(NotFoundError):
        await world.documents.get(owner.id, yellow.id)


def _all() -> DocumentFilter:
    return DocumentFilter()


async def test_admins_file_into_any_drawer(world: World) -> None:
    admin = await world.user("admin", role=Role.ADMIN)
    owner = await world.user()
    office = await world.drawers.create(owner.id, "Office")
    received = await world.pipeline().receive(
        admin.id, incoming(b"%PDF-1.7 b"), filename="b.pdf", drawer=office.id
    )
    await world.drain()
    document = await world.documents.get(admin.id, received.id)
    assert (document.drawer_id, document.lane) == (office.id, Lane.GREEN)
    # The drawer's owner sees it, as any green document in their drawer.
    assert (await world.documents.get(owner.id, received.id)).id == received.id
    user = await world.user()
    with pytest.raises(NotFoundError):
        await world.pipeline().receive(
            user.id, incoming(b"%PDF-1.7 c"), filename="c.pdf", drawer=office.id
        )


async def test_admins_confirm_into_any_drawer(world: World) -> None:
    admin, boss = await _second_admin(world)
    owner, stranger = await world.user(), await world.user()
    foreign = await world.drawers.create(stranger.id, "Foreign")
    rules = await rule_world(world)
    pipeline = rules.pipeline()
    received = await pipeline.receive(owner.id, incoming(b"%PDF-1.7 d"), filename="d.pdf")
    await world.drain(rules.pipeline(Returns(UNCERTAIN)))
    with pytest.raises(NotFoundError):
        await pipeline.confirm(owner.id, received.id, DocumentChanges(), drawer=foreign.id)

    await pipeline.confirm(
        admin.id, received.id, DocumentChanges(), accept_suggestions=True, drawer=foreign.id
    )
    await world.drain(pipeline)
    filed = await world.documents.get(owner.id, received.id)
    assert (filed.drawer_id, filed.lane) == (foreign.id, Lane.GREEN)
    assert (await world.documents.get(stranger.id, received.id)).id == received.id

    # The admin's choice holds while they are an admin; then filing asks the owner again.
    await pipeline.reprocess_from(owner.id, received.id, Step.APPLY_RULES)
    await world.drain(pipeline)
    assert (await world.documents.get(owner.id, received.id)).lane is Lane.GREEN
    await world.users.set_active(boss.id, admin.id, False)
    await pipeline.reprocess_from(owner.id, received.id, Step.APPLY_RULES)
    await world.drain(pipeline)
    assert (await world.documents.get(owner.id, received.id)).lane is Lane.YELLOW


async def test_admins_change_user_rules_for_their_owner(world: World) -> None:
    r = await rule_world(world)
    owner, stranger = await world.user(), await world.user()
    tag = await r.tag("t")
    rule = await r.user_rule(owner, definition("Title", channel_api(), add_tags(tag.id)))
    owners = await world.drawers.create(owner.id, "Owner's")
    foreign = await world.drawers.create(stranger.id, "Stranger's")

    # References are checked for the rule's owner, not for the admin (who may write anywhere).
    with pytest.raises(NotFoundError):
        await r.rules.change(
            r.admin.id, rule.id, definition("File", channel_api(), SetDrawer(foreign.id))
        )
    changed = await r.rules.change(
        r.admin.id, rule.id, definition("File", channel_api(), SetDrawer(owners.id))
    )
    assert (changed.scope, changed.owner_id, changed.current.created_by) == (
        RuleScope.USER,
        owner.id,
        r.admin.id,
    )
    await r.rules.delete(r.admin.id, rule.id)
    with pytest.raises(NotFoundError):
        await r.rules.get(owner.id, rule.id)
    # Nobody else changes it.
    other = await r.user_rule(owner, definition("Other", channel_api(), add_tags(tag.id)))
    with pytest.raises(NotFoundError):
        await r.rules.change(
            stranger.id, other.id, definition("X", channel_api(), add_tags(tag.id))
        )


async def test_admins_apply_a_global_rule_to_every_document(world: World) -> None:
    r = await rule_world(world)
    alice, bob = await world.user(), await world.user()
    tax = await r.tag("tax")
    theirs = [await r.arrive(alice), await r.arrive(bob)]
    rule = await r.global_rule(definition("Tax", channel_api(), add_tags(tax.id)))

    preview = await r.applications.preview(r.admin.id, rule.id)
    assert {item.document.id for item in preview.items} == {d.id for d in theirs}
    # A user applies it to what they may write to only.
    assert [
        item.document.id for item in (await r.applications.preview(alice.id, rule.id)).items
    ] == [theirs[0].id]

from datetime import date

import pytest

from papiq.core.domain.attributes import AttributeType
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.events import DocumentDeleted, DocumentFiled, DocumentUpdated
from papiq.core.domain.ids import ContactId, DocumentId, TagId, new_id
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.domain.users import Role, User
from tests.builders import UNCERTAIN
from tests.unit.services.conftest import Returns, World


class Scene:
    """Owner with a shared drawer (reader: read, writer: read_write) and a green document."""

    owner: User
    reader: User
    writer: User
    stranger: User
    shared: Drawer
    document: Document


@pytest.fixture
async def scene(world: World) -> Scene:
    scene = Scene()
    scene.owner, scene.reader = await world.user("owner"), await world.user("reader")
    scene.writer, scene.stranger = await world.user("writer"), await world.user("stranger")
    scene.shared = await world.drawers.create(scene.owner.id, "Household")
    await world.drawers.share(scene.owner.id, scene.shared.id, scene.reader.id, ShareLevel.READ)
    await world.drawers.share(
        scene.owner.id, scene.shared.id, scene.writer.id, ShareLevel.READ_WRITE
    )
    received = await world.pipeline().receive(
        scene.owner.id,
        b"%PDF electricity",
        filename="electricity.pdf",
        media_type="application/pdf",
        drawer=scene.shared.id,
    )
    await world.drain()
    scene.document = await world.documents.get(scene.owner.id, received.id)
    assert scene.document.lane is Lane.GREEN
    world.database.outbox.clear()
    return scene


async def test_visibility(world: World, scene: Scene) -> None:
    for user in (scene.owner, scene.reader, scene.writer):
        assert (await world.documents.get(user.id, scene.document.id)).id == scene.document.id
        assert [d.id for d in await world.documents.list_visible(user.id)] == [scene.document.id]
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.stranger.id, scene.document.id)
    assert await world.documents.list_visible(scene.stranger.id) == []
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.owner.id, DocumentId(new_id()))


async def test_yellow_document_is_hidden_from_shares(world: World, scene: Scene) -> None:
    await world.pipeline().reprocess_from(scene.owner.id, scene.document.id, Step.CLASSIFY)
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.reader.id, scene.document.id)  # in processing
    await world.drain(world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)}))
    assert (await world.documents.get(scene.owner.id, scene.document.id)).lane is Lane.YELLOW
    for user in (scene.reader, scene.writer):
        with pytest.raises(NotFoundError):
            await world.documents.get(user.id, scene.document.id)
        assert await world.documents.list_visible(user.id) == []


async def test_writer_updates_metadata(world: World, scene: Scene) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Stadtwerke")
    tag = await world.master_data.create_tag(admin.id, "Energy")
    note = await world.master_data.create_attribute(admin.id, "Meter", AttributeType.TEXT)
    updated = await world.documents.update_metadata(
        scene.writer.id,
        scene.document.id,
        DocumentChanges(
            contact_id=contact.id,
            tag_ids=frozenset({tag.id}),
            document_date=date(2026, 9, 30),
            attributes={note.id: "4711"},
        ),
    )
    assert updated.contact_id == contact.id
    assert updated.attributes == {note.id: "4711"}
    stored = await world.documents.get(scene.owner.id, scene.document.id)
    assert stored == updated
    (event,) = world.events()
    assert isinstance(event, DocumentUpdated)
    assert event.fields == ("contact_id", "tag_ids", "document_date", "attributes")


async def test_reader_cannot_update(world: World, scene: Scene) -> None:
    with pytest.raises(PermissionDeniedError):
        await world.documents.update_metadata(
            scene.reader.id, scene.document.id, DocumentChanges(title="Mine")
        )
    with pytest.raises(NotFoundError):
        await world.documents.update_metadata(
            scene.stranger.id, scene.document.id, DocumentChanges(title="Mine")
        )
    assert world.events() == []


async def test_references_must_exist(world: World, scene: Scene) -> None:
    for changes in (
        DocumentChanges(contact_id=ContactId(new_id())),
        DocumentChanges(tag_ids=frozenset({TagId(new_id())})),
    ):
        with pytest.raises(NotFoundError):
            await world.documents.update_metadata(scene.owner.id, scene.document.id, changes)


async def test_attribute_values_are_checked(world: World, scene: Scene) -> None:
    admin = await world.user(role=Role.ADMIN)
    total = await world.master_data.create_attribute(admin.id, "Total", AttributeType.NUMBER)
    with pytest.raises(ValidationError):
        await world.documents.update_metadata(
            scene.owner.id, scene.document.id, DocumentChanges(attributes={total.id: "12"})
        )
    assert world.events() == []


async def test_move_needs_write_access_to_the_target(world: World, scene: Scene) -> None:
    writer_default = await world.default_drawer(scene.writer)
    owner_default = await world.default_drawer(scene.owner)
    with pytest.raises(NotFoundError):
        await world.documents.move(scene.writer.id, scene.document.id, owner_default.id)
    read_only = await world.drawers.create(scene.stranger.id, "Archive")
    await world.drawers.share(scene.stranger.id, read_only.id, scene.writer.id, ShareLevel.READ)
    with pytest.raises(PermissionDeniedError):
        await world.documents.move(scene.writer.id, scene.document.id, read_only.id)
    with pytest.raises(PermissionDeniedError):
        await world.documents.move(scene.reader.id, scene.document.id, scene.shared.id)

    moved = await world.documents.move(scene.writer.id, scene.document.id, writer_default.id)
    assert moved.drawer_id == writer_default.id
    (event,) = world.events()
    assert isinstance(event, DocumentFiled) and event.drawer_id == writer_default.id
    # Owner keeps access; the reader lost it with the move.
    assert (await world.documents.get(scene.owner.id, scene.document.id)).id == moved.id
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.reader.id, scene.document.id)


async def test_only_the_owner_deletes(world: World, scene: Scene) -> None:
    with pytest.raises(PermissionDeniedError):
        await world.documents.delete(scene.writer.id, scene.document.id)
    await world.documents.delete(scene.owner.id, scene.document.id)
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.owner.id, scene.document.id)
    (event,) = world.events()
    assert isinstance(event, DocumentDeleted)
    assert event.document_id == scene.document.id


async def test_processing_log_is_readable_with_the_document(world: World, scene: Scene) -> None:
    entries = await world.documents.processing_log(scene.reader.id, scene.document.id)
    assert [entry.step for entry in entries] == list(Step)
    with pytest.raises(NotFoundError):
        await world.documents.processing_log(scene.stranger.id, scene.document.id)

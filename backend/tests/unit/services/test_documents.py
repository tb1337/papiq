from datetime import date
from pathlib import Path

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
from papiq.core.domain.ids import ContactId, DocumentId, DrawerId, TagId, UserId, new_id
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.domain.users import Role, User
from papiq.core.ports import DocumentFilter
from papiq.core.services.documents import DocumentFile, FileInfo
from papiq.core.services.objects import archive_key, preview_key
from tests.builders import UNCERTAIN, incoming
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
        incoming(b"%PDF-1.7 electricity"),
        filename="electricity.pdf",
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


async def test_owner_moves_into_drawers_with_write_access(world: World, scene: Scene) -> None:
    owner_default = await world.default_drawer(scene.owner)
    read_only = await world.drawers.create(scene.stranger.id, "Archive")
    await world.drawers.share(scene.stranger.id, read_only.id, scene.owner.id, ShareLevel.READ)
    with pytest.raises(PermissionDeniedError):
        await world.documents.move(scene.owner.id, scene.document.id, read_only.id)
    with pytest.raises(NotFoundError):
        await world.documents.move(
            scene.owner.id, scene.document.id, (await world.default_drawer(scene.writer)).id
        )

    await world.documents.move(scene.owner.id, scene.document.id, owner_default.id)
    moved = await world.documents.get(scene.owner.id, scene.document.id)
    assert moved.drawer_id == owner_default.id
    (event,) = world.events()
    assert isinstance(event, DocumentFiled) and event.drawer_id == owner_default.id
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.reader.id, scene.document.id)


async def test_shares_never_allow_moving(world: World, scene: Scene) -> None:
    writer_default = await world.default_drawer(scene.writer)
    with pytest.raises(PermissionDeniedError):
        await world.documents.move(scene.writer.id, scene.document.id, writer_default.id)
    with pytest.raises(PermissionDeniedError):
        await world.documents.move(scene.writer.id, scene.document.id, scene.shared.id)
    with pytest.raises(NotFoundError):
        await world.documents.move(scene.stranger.id, scene.document.id, scene.shared.id)
    assert world.events() == []


async def test_admin_moves_any_document_into_any_drawer(world: World, scene: Scene) -> None:
    admin = await world.user(role=Role.ADMIN)
    stranger_default = await world.default_drawer(scene.stranger)
    await world.documents.move(admin.id, scene.document.id, stranger_default.id)
    assert (await world.documents.get(scene.owner.id, scene.document.id)).drawer_id == (
        stranger_default.id
    )
    with pytest.raises(NotFoundError):
        await world.documents.get(admin.id, scene.document.id)  # moving grants no read access
    with pytest.raises(NotFoundError):
        await world.documents.move(admin.id, DocumentId(new_id()), stranger_default.id)
    with pytest.raises(NotFoundError):
        await world.documents.move(admin.id, scene.document.id, DrawerId(new_id()))


async def test_only_the_owner_deletes(world: World, scene: Scene) -> None:
    with pytest.raises(PermissionDeniedError):
        await world.documents.delete(scene.writer.id, scene.document.id)
    await world.documents.delete(scene.owner.id, scene.document.id)
    with pytest.raises(NotFoundError):
        await world.documents.get(scene.owner.id, scene.document.id)
    (event,) = world.events()
    assert isinstance(event, DocumentDeleted)
    assert event.document_id == scene.document.id


async def test_only_the_owner_reads_the_processing_log(world: World, scene: Scene) -> None:
    entries = await world.documents.processing_log(scene.owner.id, scene.document.id)
    assert [entry.step for entry in entries] == list(Step)
    for user in (scene.reader, scene.writer):
        with pytest.raises(PermissionDeniedError, match="only the owner"):
            await world.documents.processing_log(user.id, scene.document.id)
    with pytest.raises(NotFoundError):
        await world.documents.processing_log(scene.stranger.id, scene.document.id)


async def test_filter_readers_follows_the_visibility(world: World, scene: Scene) -> None:
    everyone = {user.id for user in (scene.owner, scene.reader, scene.writer, scene.stranger)}
    unknown = UserId(new_id())
    readers = await world.documents.filter_readers(scene.document.id, {*everyone, unknown})
    assert readers == {scene.owner.id, scene.reader.id, scene.writer.id}

    await world.pipeline().reprocess_from(scene.owner.id, scene.document.id, Step.CLASSIFY)
    assert await world.documents.filter_readers(scene.document.id, everyone) == {scene.owner.id}
    assert await world.documents.filter_readers(DocumentId(new_id()), everyone) == set()
    assert await world.documents.filter_readers(scene.document.id, set()) == set()


async def test_views_carry_the_access(world: World, scene: Scene) -> None:
    expected = [
        (scene.owner, ShareLevel.READ_WRITE),
        (scene.reader, ShareLevel.READ),
        (scene.writer, ShareLevel.READ_WRITE),
    ]
    for user, access in expected:
        assert (await world.documents.view(user.id, scene.document.id)).access is access
        [view] = await world.documents.query(user.id, DocumentFilter())
        assert view.access is access and view.document.id == scene.document.id
    with pytest.raises(NotFoundError):
        await world.documents.view(scene.stranger.id, scene.document.id)
    assert await world.documents.query(scene.stranger.id, DocumentFilter()) == []


async def test_query_pages_newest_first(world: World, scene: Scene) -> None:
    for number in range(4):
        await world.pipeline().receive(
            scene.owner.id, incoming(f"%PDF-1.7 page {number}".encode()), filename=f"{number}.pdf"
        )
    everything = await world.documents.query(scene.owner.id, DocumentFilter(), limit=500)
    assert len(everything) == 5
    first = await world.documents.query(scene.owner.id, DocumentFilter(), limit=2)
    rest = await world.documents.query(
        scene.owner.id, DocumentFilter(), before=first[-1].document.id, limit=200
    )
    assert [v.document.id for v in [*first, *rest]] == [v.document.id for v in everything]
    # Documents in processing are the owner's only.
    assert len(await world.documents.query(scene.reader.id, DocumentFilter())) == 1


async def test_downloads_need_read_access(world: World, scene: Scene, tmp_path: Path) -> None:
    target = tmp_path / "file"
    info = await world.documents.download(
        scene.reader.id, scene.document.id, DocumentFile.ORIGINAL, target
    )
    assert info == FileInfo("application/pdf", "electricity.pdf")
    assert target.read_bytes() == b"%PDF-1.7 electricity"
    with pytest.raises(NotFoundError):  # not made by the placeholder steps
        await world.documents.download(
            scene.owner.id, scene.document.id, DocumentFile.ARCHIVE, target
        )
    await world.object_store.put(archive_key(scene.document.id), b"%PDF-A", content_type="x")
    info = await world.documents.download(
        scene.owner.id, scene.document.id, DocumentFile.ARCHIVE, target
    )
    assert info == FileInfo("application/pdf", "electricity.pdf")
    await world.object_store.put(preview_key(scene.document.id), b"RIFF", content_type="x")
    info = await world.documents.download(
        scene.writer.id, scene.document.id, DocumentFile.PREVIEW, target
    )
    assert info == FileInfo("image/webp", "electricity.webp")
    for file in DocumentFile:
        with pytest.raises(NotFoundError):
            await world.documents.download(scene.stranger.id, scene.document.id, file, target)

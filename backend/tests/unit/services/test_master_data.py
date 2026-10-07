import pytest

from papiq.core.domain.attributes import AttributeType
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import ContactId, DocumentTypeId, new_id
from papiq.core.domain.users import Role
from tests.builders import incoming
from tests.unit.services.conftest import World


async def test_admin_maintains_master_data(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    contact = await service.create_contact(admin.id, "Stadtwerke")
    invoice = await service.create_document_type(admin.id, "Invoice")
    tag = await service.create_tag(admin.id, "Tax")
    total = await service.create_attribute(
        admin.id, "Total", AttributeType.AMOUNT, document_type_ids=[invoice.id]
    )
    period = await service.create_attribute(
        admin.id, "Period", AttributeType.CHOICE, choices=["monthly", "yearly"]
    )
    assert (await service.rename_contact(admin.id, contact.id, "SWK")).name == "SWK"
    assert (await service.rename_document_type(admin.id, invoice.id, "Bill")).name == "Bill"
    assert (await service.rename_tag(admin.id, tag.id, "Taxes")).name == "Taxes"
    assert (await service.rename_attribute(admin.id, total.id, "Sum")).name == "Sum"
    async with world.uow() as uow:
        assert (await uow.contacts.get(contact.id)).name == "SWK"
        assert (await uow.attributes.get(total.id)).document_type_ids == frozenset({invoice.id})
        assert (await uow.attributes.get(period.id)).is_global


async def test_only_admins_change_master_data(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    contact = await service.create_contact(admin.id, "Bank")
    with pytest.raises(PermissionDeniedError):
        await service.create_contact(user.id, "Other bank")
    with pytest.raises(PermissionDeniedError):
        await service.rename_contact(user.id, contact.id, "Mine")
    with pytest.raises(PermissionDeniedError):
        await service.create_document_type(user.id, "Letter")
    with pytest.raises(PermissionDeniedError):
        await service.create_tag(user.id, "x")
    with pytest.raises(PermissionDeniedError):
        await service.create_attribute(user.id, "x", AttributeType.TEXT)


async def test_names_are_unique_per_kind(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    await service.create_tag(admin.id, "Insurance")
    await service.create_contact(admin.id, "Insurance")
    with pytest.raises(ConflictError):
        await service.create_tag(admin.id, "insurance")
    other = await service.create_tag(admin.id, "Car")
    with pytest.raises(ConflictError):
        await service.rename_tag(admin.id, other.id, "INSURANCE")
    assert (await service.rename_tag(admin.id, other.id, "CAR")).name == "CAR"


async def test_attribute_needs_existing_document_types(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        await world.master_data.create_attribute(
            admin.id, "Total", AttributeType.NUMBER, document_type_ids=[DocumentTypeId(new_id())]
        )


async def test_everyone_reads_master_data_sorted(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    for name in ("b", "A", "c"):
        await world.master_data.create_tag(admin.id, name)
    tags = await world.master_data.list_tags(user.id)
    assert [tag.name for tag in tags] == ["A", "b", "c"]
    assert (await world.master_data.get_tag(user.id, tags[0].id)).name == "A"
    contact = await world.master_data.create_contact(admin.id, "ACME")
    assert await world.master_data.list_contacts(user.id) == [contact]
    invoice = await world.master_data.create_document_type(admin.id, "Invoice")
    assert await world.master_data.get_document_type(user.id, invoice.id) == invoice
    note = await world.master_data.create_attribute(admin.id, "Note", AttributeType.TEXT)
    assert await world.master_data.list_attributes(user.id) == [note]
    with pytest.raises(NotFoundError):
        await world.master_data.get_contact(user.id, ContactId(new_id()))


async def test_only_unused_master_data_is_deleted_by_admins(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    contact = await world.master_data.create_contact(admin.id, "ACME")
    tag = await world.master_data.create_tag(admin.id, "tax")
    invoice = await world.master_data.create_document_type(admin.id, "Invoice")
    bound = await world.master_data.create_attribute(
        admin.id, "Amount", AttributeType.AMOUNT, document_type_ids=[invoice.id]
    )
    unused = await world.master_data.create_tag(admin.id, "unused")
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    await world.drain()
    await world.documents.update_metadata(
        owner.id,
        document.id,
        DocumentChanges(contact_id=contact.id, tag_ids=frozenset({tag.id})),
    )
    with pytest.raises(PermissionDeniedError):
        await world.master_data.delete_tag(owner.id, unused.id)
    for delete in (
        world.master_data.delete_contact(admin.id, contact.id),
        world.master_data.delete_tag(admin.id, tag.id),
        world.master_data.delete_document_type(admin.id, invoice.id),  # an attribute uses it
    ):
        with pytest.raises(ConflictError):
            await delete
    await world.master_data.delete_tag(admin.id, unused.id)
    await world.master_data.delete_attribute(admin.id, bound.id)
    await world.master_data.delete_document_type(admin.id, invoice.id)
    assert [t.name for t in await world.master_data.list_tags(admin.id)] == ["tax"]
    with pytest.raises(NotFoundError):
        await world.master_data.delete_tag(admin.id, unused.id)

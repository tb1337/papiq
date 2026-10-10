import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.fields import FieldType
from papiq.core.domain.ids import ContactId, DocumentTypeId, new_id
from papiq.core.domain.users import Role
from papiq.core.services.master_data import learn_alias
from tests.builders import incoming
from tests.unit.services.conftest import World


async def test_admin_maintains_master_data(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    contact = await service.create_contact(admin.id, "Stadtwerke")
    invoice = await service.create_document_type(admin.id, "Invoice")
    tag = await service.create_tag(admin.id, "Tax")
    total = await service.create_field(
        admin.id, "Total", FieldType.AMOUNT, document_type_ids=[invoice.id]
    )
    period = await service.create_field(
        admin.id, "Period", FieldType.CHOICE, choices=["monthly", "yearly"]
    )
    assert (await service.rename_contact(admin.id, contact.id, "SWK")).name == "SWK"
    assert (await service.rename_document_type(admin.id, invoice.id, "Bill")).name == "Bill"
    assert (await service.rename_tag(admin.id, tag.id, "Taxes")).name == "Taxes"
    assert (await service.rename_field(admin.id, total.id, "Sum")).name == "Sum"
    async with world.uow() as uow:
        assert (await uow.contacts.get(contact.id)).name == "SWK"
        assert (await uow.fields.get(total.id)).document_type_ids == frozenset({invoice.id})
        assert (await uow.fields.get(period.id)).is_global


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
        await service.create_field(user.id, "x", FieldType.TEXT)


async def test_contact_aliases_and_type_descriptions(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    inter = await service.create_contact(admin.id, "INTER Versicherungsgruppe", ["INTER AG"])
    other = await service.create_contact(admin.id, "Allianz")
    changed = await service.change_contact(
        admin.id, inter.id, aliases=["INTER Krankenversicherung AG", "inter krankenversicherung ag"]
    )
    assert changed.aliases == ["INTER Krankenversicherung AG"]
    assert (await service.rename_contact(admin.id, inter.id, "INTER")).aliases == changed.aliases
    # A name or an alias of one contact is never a name or an alias of another.
    for change in (
        service.change_contact(admin.id, other.id, aliases=["Inter"]),
        service.change_contact(admin.id, other.id, name="inter krankenversicherung ag"),
        service.create_contact(admin.id, "Other", ["INTER Krankenversicherung AG"]),
        # As classification compares names: legal forms and punctuation do not count.
        service.create_contact(admin.id, "Other", ["INTER Krankenversicherung"]),
        service.create_contact(admin.id, "INTER Krankenversicherung GmbH"),
    ):
        with pytest.raises(ConflictError):
            await change
    pay = await service.create_document_type(admin.id, "Pay slip", " Entgeltbescheinigung ")
    assert pay.description == "Entgeltbescheinigung"
    kept = await service.change_document_type(admin.id, pay.id, name="Payslip")
    assert kept.description == "Entgeltbescheinigung"
    assert (
        await service.change_document_type(admin.id, pay.id, description="")
    ).description is None


async def test_learn_alias(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    inter = await service.create_contact(admin.id, "INTER Versicherungsgruppe")
    allianz = await service.create_contact(admin.id, "Allianz", ["INTER Kranken"])
    async with world.uow() as uow:
        learned = await learn_alias(uow, inter.id, "INTER Krankenversicherung AG")
        assert learned is not None and learned.aliases == ["INTER Krankenversicherung AG"]
        assert await learn_alias(uow, inter.id, "inter versicherungsgruppe") is None  # its name
        assert await learn_alias(uow, inter.id, "ALLIANZ") is None  # another contact's name
        assert await learn_alias(uow, inter.id, "Allianz SE") is None  # the same, compared
        assert await learn_alias(uow, inter.id, "Max Mustermann") is None  # unrelated
        assert await learn_alias(uow, inter.id, "GmbH") is None  # nothing to compare
        assert await learn_alias(uow, inter.id, "INTER\nKranken") is not None  # moves
        await uow.commit()
    async with world.uow() as uow:
        assert (await uow.contacts.get(inter.id)).aliases == [
            "INTER Krankenversicherung AG",
            "INTER Kranken",
        ]
        assert (await uow.contacts.get(allianz.id)).aliases == []


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


async def test_field_needs_existing_document_types(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        await world.master_data.create_field(
            admin.id, "Total", FieldType.NUMBER, document_type_ids=[DocumentTypeId(new_id())]
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
    note = await world.master_data.create_field(admin.id, "Note", FieldType.TEXT)
    assert await world.master_data.list_fields(user.id) == [note]
    with pytest.raises(NotFoundError):
        await world.master_data.get_contact(user.id, ContactId(new_id()))


async def test_only_unused_master_data_is_deleted_by_admins(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    contact = await world.master_data.create_contact(admin.id, "ACME")
    tag = await world.master_data.create_tag(admin.id, "tax")
    invoice = await world.master_data.create_document_type(admin.id, "Invoice")
    bound = await world.master_data.create_field(
        admin.id, "Amount", FieldType.AMOUNT, document_type_ids=[invoice.id]
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
        world.master_data.delete_document_type(admin.id, invoice.id),  # a field uses it
    ):
        with pytest.raises(ConflictError):
            await delete
    await world.master_data.delete_tag(admin.id, unused.id)
    await world.master_data.delete_field(admin.id, bound.id)
    await world.master_data.delete_document_type(admin.id, invoice.id)
    assert [t.name for t in await world.master_data.list_tags(admin.id)] == ["tax"]
    with pytest.raises(NotFoundError):
        await world.master_data.delete_tag(admin.id, unused.id)


async def test_field_definitions_change_without_breaking_values(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    invoice = await world.master_data.create_document_type(admin.id, "Invoice")
    letter = await world.master_data.create_document_type(admin.id, "Letter")
    kind = await world.master_data.create_field(
        admin.id, "Kind", FieldType.CHOICE, choices=["a", "b"]
    )
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    await world.drain()
    await world.documents.update_metadata(
        owner.id,
        document.id,
        DocumentChanges(document_type_id=invoice.id, fields={kind.id: "a"}),
    )
    change = world.master_data.change_field

    with pytest.raises(PermissionDeniedError):
        await change(owner.id, kind.id, name="Sort")
    changed = await change(admin.id, kind.id, name="Sort", choices=["a", "b", "c"])
    assert changed.name == "Sort" and changed.choices == ("a", "b", "c")
    await change(admin.id, kind.id, choices=["c", "a"])  # b is unused
    with pytest.raises(ConflictError):
        await change(admin.id, kind.id, choices=["c"])  # a is in use
    with pytest.raises(ConflictError):
        await change(admin.id, kind.id, document_type_ids=[letter.id])  # narrower
    await change(admin.id, kind.id, document_type_ids=[invoice.id, letter.id])
    await change(admin.id, kind.id, document_type_ids=[invoice.id])  # values all inside
    await change(admin.id, kind.id, document_type_ids=None)
    with pytest.raises(NotFoundError):
        await change(admin.id, kind.id, document_type_ids=[DocumentTypeId(new_id())])
    stored = await world.master_data.get_field(owner.id, kind.id)
    assert stored.choices == ("c", "a") and stored.document_type_ids is None
    document_now = await world.documents.get(owner.id, document.id)
    assert document_now.fields == {kind.id: "a"}

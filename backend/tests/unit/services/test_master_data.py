import asyncio
from datetime import date

import pytest

from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.fields import FieldType
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.domain.master_data import Assignment, TagScope
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.rules import (
    AddTags,
    Condition,
    ConditionField,
    Group,
    Operator,
    Rule,
    RuleDefinition,
    RuleScope,
)
from papiq.core.domain.users import Role
from papiq.core.services import master_data
from papiq.core.services.master_data import learn_alias, replace_learned_identifiers
from tests import builders
from tests.builders import incoming
from tests.unit.services.conftest import World

MANUAL, LEARNED = IdentifierSource.MANUAL, IdentifierSource.LEARNED
REJECTED = IdentifierSource.REJECTED


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


async def test_no_two_fields_share_a_key_in_templates(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    gross = await service.create_field(admin.id, "LA_Brutto", FieldType.AMOUNT)
    assert gross.key == "la_brutto"
    with pytest.raises(ConflictError, match=r"field\.la_brutto"):
        await service.create_field(admin.id, "LA Brutto", FieldType.AMOUNT)
    net = await service.create_field(admin.id, "LA Netto", FieldType.AMOUNT)
    with pytest.raises(ConflictError, match=r"field\.la_brutto"):
        await service.change_field(admin.id, net.id, name="la-brutto")
    # The field keeps its own key when only the case of its name changes.
    assert (await service.change_field(admin.id, gross.id, name="LA_BRUTTO")).key == "la_brutto"


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
    example = await service.create_contact(admin.id, "Nord Versicherungsgruppe", ["Nord AG"])
    other = await service.create_contact(admin.id, "Muster")
    changed = await service.change_contact(
        admin.id,
        example.id,
        aliases=["Nord Krankenversicherung AG", "nord krankenversicherung ag"],
    )
    assert changed.aliases == ["Nord Krankenversicherung AG"]
    assert (await service.rename_contact(admin.id, example.id, "Nord")).aliases == changed.aliases
    # A name or an alias of one contact is never a name or an alias of another.
    for change in (
        service.change_contact(admin.id, other.id, aliases=["Nord"]),
        service.change_contact(admin.id, other.id, name="nord krankenversicherung ag"),
        service.create_contact(admin.id, "Other", ["Nord Krankenversicherung AG"]),
        # As classification compares names: legal forms and punctuation do not count.
        service.create_contact(admin.id, "Other", ["Nord Krankenversicherung"]),
        service.create_contact(admin.id, "Nord Krankenversicherung GmbH"),
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


IBAN = Identifier.parse(IdentifierKind.IBAN, "DE89 3704 0044 0532 0130 00")
DOMAIN = Identifier.parse(IdentifierKind.EMAIL_DOMAIN, "stadtwerke.example")


async def test_contact_identifiers(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    utility = await service.create_contact(admin.id, "Stadtwerke", identifiers=[IBAN])
    other = await service.create_contact(admin.id, "Bank")
    assert utility.identifiers == [IBAN]
    # A value another contact entered by hand is a conflict.
    with pytest.raises(ConflictError):
        await service.change_contact(admin.id, other.id, identifiers=[IBAN])
    with pytest.raises(ConflictError):
        await service.create_contact(admin.id, "Other", identifiers=[IBAN])
    # Rejected and entered ones are replaced on their own.
    changed = await service.change_contact(admin.id, utility.id, rejected_identifiers=[DOMAIN])
    assert [item.source for item in changed.identifiers] == [MANUAL, REJECTED]
    changed = await service.change_contact(admin.id, utility.id, identifiers=[])
    assert changed.identifiers == [Identifier(DOMAIN.kind, DOMAIN.value, REJECTED)]
    # Entered by hand elsewhere, a rejected (or learned) value moves.
    moved = await service.change_contact(admin.id, other.id, identifiers=[DOMAIN])
    assert moved.identifiers == [DOMAIN]
    async with world.uow() as uow:
        assert (await uow.contacts.get(utility.id)).identifiers == []


async def test_training_replaces_the_learned_identifiers(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    utility = await service.create_contact(admin.id, "Stadtwerke")
    bank = await service.create_contact(admin.id, "Bank", identifiers=[IBAN])
    learned = [Identifier(DOMAIN.kind, DOMAIN.value, LEARNED)]
    taken = [Identifier(IBAN.kind, IBAN.value, LEARNED)]
    async with world.uow() as uow:
        assert await replace_learned_identifiers(uow, {utility.id: [*learned, *taken]}) == 1
        await uow.commit()
    async with world.uow() as uow:
        assert (await uow.contacts.get(utility.id)).identifiers == learned
        assert (await uow.contacts.get(bank.id)).identifiers == [IBAN]
        # The value moves to another contact in one go.
        assert await replace_learned_identifiers(uow, {bank.id: learned}) == 1
        await uow.commit()
    async with world.uow() as uow:
        assert (await uow.contacts.get(utility.id)).identifiers == []
        assert (await uow.contacts.get(bank.id)).identifiers == [IBAN, *learned]


async def test_required_fields_and_title_patterns(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    payslip = await service.create_document_type(
        admin.id, "Lohnnachweis", title_pattern="Lohn {{ document_date | date: '%Y-%m' }}"
    )
    other = await service.create_document_type(admin.id, "Brief")
    period = await service.create_field(
        admin.id,
        "Zeitraum",
        FieldType.TEXT,
        document_type_ids=[payslip.id, other.id],
        required_document_type_ids=[payslip.id],
    )
    assert period.is_required_for(payslip.id) and not period.is_required_for(other.id)
    note = await service.create_field(admin.id, "Notiz", FieldType.TEXT, required=True)
    assert note.is_required_for(None)
    # A pattern names fields of the type only.
    changed = await service.change_document_type(
        admin.id, payslip.id, title_pattern="Lohn {{ field.zeitraum }} {{ field.notiz }}"
    )
    assert changed.title_pattern == "Lohn {{ field.zeitraum }} {{ field.notiz }}"
    with pytest.raises(ValidationError, match=r"field\.betrag"):
        await service.change_document_type(admin.id, payslip.id, title_pattern="{{ field.betrag }}")
    with pytest.raises(ValidationError, match="invalid template"):
        await service.change_document_type(admin.id, payslip.id, title_pattern="{{ contact")
    with pytest.raises(ValidationError):
        await service.create_document_type(admin.id, "Neu", title_pattern="{{ field.zeitraum }}")
    # Renamed, the field is renamed in the patterns; narrowed, it stays required for the rest.
    await service.rename_field(admin.id, period.id, "Abrechnungszeitraum")
    async with world.uow() as uow:
        assert (await uow.document_types.get(payslip.id)).title_pattern == (
            "Lohn {{ field.abrechnungszeitraum }} {{ field.notiz }}"
        )
    narrowed = await service.change_field(admin.id, period.id, document_type_ids=[payslip.id])
    assert narrowed.required_types == frozenset({payslip.id})
    # Not narrowed past a type whose pattern uses it, not deleted while a pattern does.
    with pytest.raises(ConflictError, match="the title pattern of 'Lohnnachweis'"):
        await service.change_field(admin.id, note.id, document_type_ids=[other.id])
    with pytest.raises(ConflictError, match="title patterns of 'Lohnnachweis'"):
        await service.delete_field(admin.id, note.id)
    optional = await service.change_field(admin.id, period.id, required_document_type_ids=[])
    assert not optional.is_required_for(payslip.id)
    with pytest.raises(ValidationError):
        await service.change_field(admin.id, period.id, required=True)


async def test_learn_alias(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    example = await service.create_contact(admin.id, "Nord Versicherungsgruppe")
    other = await service.create_contact(admin.id, "Muster", ["Nord Kranken"])
    async with world.uow() as uow:
        learned = await learn_alias(uow, example.id, "Nord Krankenversicherung AG")
        assert learned is not None and learned.aliases == ["Nord Krankenversicherung AG"]
        assert await learn_alias(uow, example.id, "nord versicherungsgruppe") is None  # its name
        assert await learn_alias(uow, example.id, "MUSTER") is None  # another contact's name
        assert await learn_alias(uow, example.id, "Muster SE") is None  # the same, compared
        assert await learn_alias(uow, example.id, "Max Mustermann") is None  # unrelated
        assert await learn_alias(uow, example.id, "GmbH") is None  # nothing to compare
        assert await learn_alias(uow, example.id, "Nord\nKranken") is not None  # moves
        await uow.commit()
    async with world.uow() as uow:
        assert (await uow.contacts.get(example.id)).aliases == [
            "Nord Krankenversicherung AG",
            "Nord Kranken",
        ]
        assert (await uow.contacts.get(other.id)).aliases == []


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


async def test_tags_nest_within_their_scope(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health", parent_id=insurance.id)
    status = await service.create_tag(admin.id, "To pay", assignment=Assignment.MANUAL)
    group = await service.create_tag(admin.id, "Insurance", scope=TagScope.CONTACT)
    assert (group.assignment, group.shown) == (Assignment.MANUAL, False)
    assert (status.assignment, status.shown) == (Assignment.MANUAL, True)
    with pytest.raises(ConflictError):  # names are unique per scope
        await service.create_tag(admin.id, "insurance", scope=TagScope.CONTACT)
    with pytest.raises(ValidationError, match="another scope"):
        await service.create_tag(admin.id, "Car", parent_id=group.id)
    with pytest.raises(ValidationError, match="below it"):
        await service.change_tag(admin.id, insurance.id, parent_id=health.id)
    with pytest.raises(ValidationError, match="by hand"):
        await service.change_tag(admin.id, group.id, assignment=Assignment.AUTO)
    with pytest.raises(NotFoundError):
        await service.create_tag(admin.id, "Car", parent_id=TagId(new_id()))
    moved = await service.change_tag(admin.id, status.id, parent_id=insurance.id, shown=False)
    assert (moved.parent_id, moved.shown, moved.name) == (insurance.id, False, "To pay")
    top = await service.change_tag(admin.id, moved.id, parent_id=None)
    assert top.parent_id is None and top.scope is TagScope.DOCUMENT
    assert [tag.name for tag in await service.list_tags(admin.id, TagScope.CONTACT)] == [
        "Insurance"
    ]
    assert len(await service.list_tags(admin.id)) == 4


async def test_contacts_and_types_are_in_groups_of_their_scope(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    health = await service.create_tag(
        admin.id, "Health insurer", scope=TagScope.CONTACT, parent_id=insurer.id
    )
    bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
    tag = await service.create_tag(admin.id, "Tax")
    contact = await service.create_contact(admin.id, "Nord", group_ids=[insurer.id, health.id])
    assert contact.group_ids == {health.id}  # a child and its parent leave the child
    with pytest.raises(ValidationError, match="contact group"):
        await service.change_contact(admin.id, contact.id, group_ids=[bills.id])
    with pytest.raises(ValidationError):
        await service.create_contact(admin.id, "Süd", group_ids=[tag.id])
    with pytest.raises(NotFoundError):
        await service.create_contact(admin.id, "Süd", group_ids=[TagId(new_id())])
    changed = await service.change_contact(admin.id, contact.id, group_ids=[insurer.id])
    assert changed.group_ids == {insurer.id}
    assert (await service.change_contact(admin.id, contact.id, name="Nord AG")).group_ids == {
        insurer.id
    }
    kind = await service.create_document_type(admin.id, "Invoice", group_ids=[bills.id])
    assert kind.group_ids == {bills.id}
    with pytest.raises(ValidationError, match="document_type group"):
        await service.change_document_type(admin.id, kind.id, group_ids=[insurer.id])
    assert (await service.change_document_type(admin.id, kind.id, group_ids=[])).group_ids == (
        frozenset()
    )


async def test_a_tag_is_deleted_only_without_tags_below_it_and_unused(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health", parent_id=insurance.id)
    insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
    ruled = await service.create_tag(admin.id, "Ruled")
    used = await service.create_tag(admin.id, "Used")
    contact = await service.create_contact(admin.id, "Nord", group_ids=[insurer.id])
    kind = await service.create_document_type(admin.id, "Invoice", group_ids=[bills.id])
    condition = Condition(field=ConditionField.TAGS, op=Operator.CONTAINS, value=str(ruled.id))
    rule = Rule.create(
        scope=RuleScope.GLOBAL,
        owner_id=None,
        definition=RuleDefinition(
            name="Tag it",
            conditions=Group(mode="all", items=(condition,)),
            actions=(AddTags(frozenset({health.id})),),
        ),
        by=admin.id,
        now=world.clock.now(),
    )
    async with world.uow() as uow:
        await uow.rules.add(rule)
        await uow.commit()
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    await world.drain()
    await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(tag_ids=frozenset({used.id}))
    )
    for tag, reason in (
        (insurance, "below it"),
        (insurer, "contacts are in"),
        (bills, "document types are in"),
        (ruled, "rules 'Tag it'"),
        (health, "rules 'Tag it'"),
        (used, "in use"),
    ):
        with pytest.raises(ConflictError, match=reason):
            await service.delete_tag(admin.id, tag.id)
    await service.change_contact(admin.id, contact.id, group_ids=[])
    await service.delete_document_type(admin.id, kind.id)
    await service.delete_tag(admin.id, insurer.id)
    await service.delete_tag(admin.id, bills.id)
    async with world.uow() as uow:
        await uow.rules.update(_deleted(await uow.rules.get(rule.id), world))
        await uow.commit()
    await service.delete_tag(admin.id, health.id)
    await service.delete_tag(admin.id, insurance.id)
    assert {tag.name for tag in await service.list_tags(admin.id)} == {"Ruled", "Used"}


def _deleted(rule: Rule, world: World) -> Rule:
    rule.delete(world.clock.now())
    return rule


async def test_documents_carry_document_tags_normalised(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health", parent_id=insurance.id)
    paid = await service.create_tag(admin.id, "Paid", assignment=Assignment.MANUAL)
    group = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    await world.drain()
    with pytest.raises(ValidationError, match="not a document tag"):
        await world.documents.update_metadata(
            owner.id, document.id, DocumentChanges(tag_ids=frozenset({group.id}))
        )
    changed = await world.documents.update_metadata(
        owner.id,
        document.id,
        DocumentChanges(tag_ids=frozenset({insurance.id, health.id, paid.id})),
    )
    assert changed.tag_ids == {health.id, paid.id}
    parent_only = await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(tag_ids=frozenset({insurance.id}))
    )
    assert parent_only.tag_ids == {insurance.id}


async def test_a_moved_tag_leaves_documents_and_groups_the_tag_below(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What carries a tag and one that a move put above it keeps the one below, as if set
    anew: a job, a page of documents per step (each step queueing the next); lane and
    confirmation stay."""
    monkeypatch.setattr(master_data, "_NORMALISE_BATCH", 2)
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health")
    dental = await service.create_tag(admin.id, "Dental", parent_id=health.id)
    other = await service.create_tag(admin.id, "Other")
    insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    kassen = await service.create_tag(admin.id, "Kassen", scope=TagScope.CONTACT)
    bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
    medical = await service.create_tag(admin.id, "Medical", scope=TagScope.DOCUMENT_TYPE)
    nord = await service.create_contact(admin.id, "Nord", group_ids=[insurer.id, kassen.id])
    invoice = await service.create_document_type(
        admin.id, "Invoice", group_ids=[bills.id, medical.id]
    )
    space = await world.default_space(owner)
    carried = [
        {insurance.id, health.id},
        {insurance.id, dental.id, other.id},
        {insurance.id},
        {health.id},
        {insurance.id, health.id},
    ]
    documents = []
    async with world.uow() as uow:
        for number, tags in enumerate(carried):
            document = builders.run_pipeline(builders.document(owner, space, content=str(number)))
            document.tag_ids = set(tags)
            await uow.documents.add(document)
            documents.append(document)
        await uow.commit()
    await world.documents.update_metadata(
        owner.id, documents[0].id, DocumentChanges(document_date=date(2026, 9, 30))
    )
    confirmed = (await world.documents.get(owner.id, documents[0].id)).confirmed_at
    assert confirmed is not None
    world.database.outbox.clear()

    await service.change_tag(admin.id, health.id, parent_id=insurance.id)
    await service.change_tag(admin.id, kassen.id, parent_id=insurer.id)
    await service.change_tag(admin.id, medical.id, parent_id=bills.id)
    steps = 0
    while await service.run_next_job():
        steps += 1
    # Health: four documents carry it or Dental, two per step, and a last step finds none.
    assert steps == 3 + 1 + 1

    async with world.uow() as uow:
        stored = [await uow.documents.get(document.id) for document in documents]
        assert (await uow.contacts.get(nord.id)).group_ids == {kassen.id}
        assert (await uow.document_types.get(invoice.id)).group_ids == {medical.id}
    assert [document.tag_ids for document in stored] == [
        {health.id},
        {dental.id, other.id},
        {insurance.id},
        {health.id},
        {health.id},
    ]
    assert [document.lane for document in stored] == [Lane.GREEN] * 5
    assert stored[0].confirmed_at == confirmed and stored[1].confirmed_at is None
    assert world.event_types() == ["document.updated"] * 3


async def test_only_a_new_parent_of_a_tag_or_group_normalises(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health", parent_id=insurance.id)
    work = await service.create_tag(admin.id, "Work", scope=TagScope.TOPIC)
    jobs = await service.create_tag(admin.id, "Jobs", scope=TagScope.TOPIC)
    await service.change_tag(admin.id, health.id, parent_id=None, name="Gesundheit")
    await service.change_tag(admin.id, jobs.id, parent_id=work.id)
    await service.change_tag(admin.id, insurance.id, shown=False)
    assert not await service.run_next_job()
    await service.change_tag(admin.id, insurance.id, parent_id=health.id)
    assert await service.run_next_job()
    # A tag gone meanwhile: the job ends without changing anything.
    async with world.uow() as uow:
        await uow.jobs.enqueue(
            master_data.NORMALISE_JOB, {"tag_id": str(new_id())}, run_at=world.clock.now()
        )
        await uow.commit()
    assert await service.run_next_job()
    assert not [job for job in world.database.jobs.values() if job.status.is_active]


async def test_each_step_of_the_normalising_sees_the_tree_as_it_is(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tag moved back while the job pages: the later steps leave its documents alone."""
    monkeypatch.setattr(master_data, "_NORMALISE_BATCH", 2)
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    insurance = await service.create_tag(admin.id, "Insurance")
    health = await service.create_tag(admin.id, "Health")
    space = await world.default_space(owner)
    documents = []
    async with world.uow() as uow:
        for number in range(4):
            document = builders.run_pipeline(builders.document(owner, space, content=str(number)))
            document.tag_ids = {insurance.id, health.id}
            await uow.documents.add(document)
            documents.append(document)
        await uow.commit()
    await service.change_tag(admin.id, health.id, parent_id=insurance.id)
    assert await service.run_next_job()  # the first two by id
    await service.change_tag(admin.id, health.id, parent_id=None)
    while await service.run_next_job():
        pass
    async with world.uow() as uow:
        stored = {
            document.id: (await uow.documents.get(document.id)).tag_ids for document in documents
        }
    newest = sorted(stored, reverse=True)  # the first step took the highest ids
    assert [stored[id] for id in newest] == [{health.id}] * 2 + [{insurance.id, health.id}] * 2


async def test_concurrent_tag_moves_make_no_cycle(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    a, b = await service.create_tag(admin.id, "A"), await service.create_tag(admin.id, "B")
    results = await asyncio.gather(
        service.change_tag(admin.id, a.id, parent_id=b.id),
        service.change_tag(admin.id, b.id, parent_id=a.id),
        return_exceptions=True,
    )
    assert sorted(type(result).__name__ for result in results) == ["Tag", "ValidationError"]


async def test_groups_and_topics_count_their_contacts_and_types(world: World) -> None:
    """Each contact (type) once at its group or topic and at those above it."""
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    health = await service.create_tag(
        admin.id, "Health insurer", scope=TagScope.CONTACT, parent_id=insurer.id
    )
    bank = await service.create_tag(admin.id, "Bank", scope=TagScope.CONTACT)
    insurance = await service.create_tag(admin.id, "Insurance", scope=TagScope.TOPIC)
    dental = await service.create_tag(
        admin.id, "Dental", scope=TagScope.TOPIC, parent_id=insurance.id
    )
    bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
    medical = await service.create_tag(
        admin.id, "Medical", scope=TagScope.DOCUMENT_TYPE, parent_id=bills.id
    )
    await service.create_contact(
        admin.id, "Nord", group_ids=[insurer.id, health.id], topic_ids=[insurance.id, dental.id]
    )
    await service.create_contact(admin.id, "Süd", group_ids=[health.id], topic_ids=[dental.id])
    await service.create_contact(admin.id, "Plain")
    await service.create_document_type(admin.id, "Invoice", group_ids=[medical.id])
    await service.create_document_type(admin.id, "Receipt", group_ids=[bills.id])
    members = await service.tag_members(admin.id)
    assert members.contacts == {insurer.id: 2, health.id: 2, insurance.id: 2, dental.id: 2}
    assert bank.id not in members.contacts
    assert members.document_types == {bills.id: 2, medical.id: 1}


async def test_tags_and_types_carry_icons_and_colours(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    tag = await service.create_tag(admin.id, "Health", icon="heart-pulse", color="#B45309")
    assert (tag.icon, tag.color) == ("heart-pulse", "#b45309")
    # Left out, they stay; None removes one.
    renamed = await service.change_tag(admin.id, tag.id, name="Health care")
    assert (renamed.icon, renamed.color) == ("heart-pulse", "#b45309")
    changed = await service.change_tag(admin.id, tag.id, icon=None, color="pink")
    assert (changed.icon, changed.color) == (None, "pink")
    with pytest.raises(ValidationError):
        await service.change_tag(admin.id, tag.id, color="purple")
    with pytest.raises(ValidationError):
        await service.create_tag(admin.id, "X", icon="Heart Pulse")

    kind = await service.create_document_type(admin.id, "Invoice", icon="receipt")
    assert kind.icon == "receipt"
    kept = await service.change_document_type(admin.id, kind.id, description="Bills")
    assert kept.icon == "receipt"
    gone = await service.change_document_type(admin.id, kind.id, icon=None)
    assert gone.icon is None
    async with world.uow() as uow:
        assert (await uow.tags.get(tag.id)).color == "pink"
        assert (await uow.document_types.get(kind.id)).icon is None
    user = await world.user()
    with pytest.raises(PermissionDeniedError):
        await service.change_tag(user.id, tag.id, icon="house")

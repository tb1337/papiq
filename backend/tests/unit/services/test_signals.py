"""The signals without a language model: facts in the text, the classifier, the neighbours."""

import itertools
from dataclasses import replace
from typing import Any

from papiq.adapters.outbound.memory import (
    BagOfWordsEmbeddings,
    FakeClassifier,
    MemorySearchIndex,
)
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import EmbeddingsError
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.master_data import Assignment, Contact, Tag
from papiq.core.domain.pipeline import NodeResult
from papiq.core.domain.search import EmbeddingStamp, IndexDocument
from papiq.core.domain.users import User
from papiq.core.ports import EmbeddingResult
from papiq.core.services.classification.signals import (
    ABSTAINED,
    ClassifierSignal,
    FactsResult,
    FactsSignal,
    NeighboursPolicy,
    NeighboursSignal,
)
from papiq.core.services.classifier import ClassifierPolicy, ClassifierService
from papiq.core.services.objects import text_key
from tests import builders
from tests.unit.services.conftest import World

THEIR_IBAN = "DE89 3704 0044 0532 0130 00"
OTHER_IBAN = "DE02 1203 0000 0000 2020 51"
TEXT = f"""\
# Stadtwerke Musterstadt GmbH
Ihr Ansprechpartner: Nord KV
Bitte überweisen Sie auf IBAN {THEIR_IBAN}. Lastschrift von {OTHER_IBAN}.
Kundennummer: 4711-0815
"""
DIMENSIONS = 256
_numbers = itertools.count()


def output(result: NodeResult | FactsResult) -> Any:
    return result.output


async def stored(world: World, owner: User, text: str, contact: Contact | None = None) -> Document:
    space = await world.default_space(owner)
    document = builders.processed(owner, space, content=f"{text} {next(_numbers)}")
    if contact is not None:
        document.apply_changes(DocumentChanges(contact_id=contact.id), {}, builders.NOW)
    async with world.uow() as uow:
        await uow.documents.add(document)
        await uow.commit()
    await world.object_store.put(text_key(document.id), text.encode(), content_type="text/md")
    return document


async def contacts(world: World, *items: Contact) -> None:
    async with world.uow() as uow:
        for item in items:
            await uow.contacts.add(item)
        await uow.commit()


# --- facts ------------------------------------------------------------------------------------


async def test_facts_are_names_and_identifiers_in_the_text(world: World) -> None:
    iban = Identifier.parse(IdentifierKind.IBAN, THEIR_IBAN)
    utility = Contact.create(name="Stadtwerke Musterstadt", now=builders.NOW, identifiers=[iban])
    insurer = Contact.create(name="Nord Versicherungsgruppe", now=builders.NOW, aliases=["Nord KV"])
    rejected = Contact.create(name="Bank", now=builders.NOW)
    rejected.set_identifiers(
        [], rejected=[Identifier.parse(IdentifierKind.CUSTOMER_NUMBER, "4711-0815")]
    )
    await contacts(world, utility, insurer, rejected)
    document = await stored(world, await world.user(), TEXT)

    result = await FactsSignal(world.uow, world.object_store).run(document)
    assert output(result)["named"] == [
        {"contact_id": str(utility.id), "name": "Stadtwerke Musterstadt", "alias": False},
        {"contact_id": str(insurer.id), "name": "Nord KV", "alias": True},
    ]
    assert output(result)["identifiers"] == [
        {"contact_id": str(utility.id), "kind": "iban", "value": iban.value}
    ]
    # The other IBAN belongs to no contact; the rejected customer number is no evidence, but
    # known.
    assert output(result)["unknown_identifiers"] == [
        {"kind": "iban", "value": "DE02120300000000202051"}
    ]


async def test_without_text_facts_abstain(world: World) -> None:
    owner = await world.user()
    document = builders.processed(owner, await world.default_space(owner))
    result = await FactsSignal(world.uow, world.object_store).run(document)
    assert ABSTAINED in result.output


# --- classifier -------------------------------------------------------------------------------


async def test_the_classifier_proposes_with_its_probabilities(world: World) -> None:
    owner = await world.user()
    utility = Contact.create(name="Stadtwerke", now=builders.NOW)
    bank = Contact.create(name="Beispielbank", now=builders.NOW)
    await contacts(world, utility, bank)
    service = ClassifierService(
        world.uow, world.object_store, world.clock, FakeClassifier(), ClassifierPolicy()
    )
    signal = ClassifierSignal(world.object_store, service)
    document = await stored(world, owner, "Stadtwerke Stromrechnung Zählerstand")
    assert output(await signal.run(document))[ABSTAINED] == "no model is trained yet"

    for number in range(2):
        for contact, text in ((utility, "Stadtwerke Stromrechnung"), (bank, "Beispielbank Saldo")):
            confirmed = await stored(world, owner, f"{text} {number}", contact)
            async with world.uow() as uow:
                current = await uow.documents.get(confirmed.id)
                current.mark_confirmed(builders.NOW)
                await uow.documents.update(current)
                await uow.commit()
    await service.train()
    result = await signal.run(document)
    assert result.model_version == "classifier 1"
    (best, probability), _ = output(result)["contacts"]
    assert best == str(utility.id) and probability > 0.5
    assert sorted(output(result)["known"]["contacts"]) == sorted([str(utility.id), str(bank.id)])
    assert output(await ClassifierSignal(world.object_store, None).run(document))[ABSTAINED]


# --- neighbours -------------------------------------------------------------------------------


class Broken:
    model = "broken"

    async def embed(self, texts: object) -> EmbeddingResult:
        raise EmbeddingsError("down")


async def test_neighbours_are_the_confirmed_documents_the_owner_may_read(world: World) -> None:
    owner, stranger = await world.user(), await world.user()
    utility = Contact.create(name="Stadtwerke", now=builders.NOW)
    await contacts(world, utility)
    embeddings = BagOfWordsEmbeddings(dimensions=DIMENSIONS)
    index = MemorySearchIndex(dimensions=DIMENSIONS)
    text = "Stadtwerke Musterstadt Stromrechnung Zählerstand Kilowattstunden"

    async def indexed(document: Document, confirmed: bool) -> None:
        if confirmed:
            async with world.uow() as uow:
                current = await uow.documents.get(document.id)
                current.mark_confirmed(builders.NOW)
                await uow.documents.update(current)
                await uow.commit()
        (vector,) = (await embeddings.embed([text])).vectors
        await index.upsert(
            [
                IndexDocument(
                    id=document.id,
                    version=1,
                    owner_id=document.owner_id,
                    space_id=document.space_id,
                    lane=document.lane,
                    title=document.title,
                    filename="scan.pdf",
                    created_at=builders.NOW,
                    contact_id=document.contact_id,
                    confirmed=confirmed,
                    vectors=(tuple(vector),),
                    embedding=EmbeddingStamp(embeddings.model, "x"),
                )
            ]
        )

    near = await stored(world, owner, text, utility)
    await indexed(near, confirmed=True)
    unconfirmed = await stored(world, owner, text, utility)
    await indexed(unconfirmed, confirmed=False)
    foreign = await stored(world, stranger, text, utility)
    await indexed(foreign, confirmed=True)
    document = await stored(world, owner, text)
    await indexed(document, confirmed=False)

    signal = NeighboursSignal(
        world.uow, world.object_store, index, embeddings, NeighboursPolicy(dimensions=DIMENSIONS)
    )
    result = await signal.run(document)
    (neighbour,) = output(result)["neighbours"]
    assert neighbour["document_id"] == str(near.id)
    assert neighbour["contact_id"] == str(utility.id)
    # What the confirmed documents carry, of every owner: what similar documents could name.
    assert output(result)["known"] == {
        "contacts": [str(utility.id)],
        "document_types": [],
        "tags": [],
        "topics": [],
    }
    # They vote on the tags the classification sets, not on those set by hand.
    electricity = Tag.create(name="Strom", now=builders.NOW)
    paid = Tag.create(name="Bezahlt", now=builders.NOW, assignment=Assignment.MANUAL)
    async with world.uow() as uow:
        await uow.tags.add(electricity)
        await uow.tags.add(paid)
        current = await uow.documents.get(near.id)
        changes = DocumentChanges(tag_ids=frozenset({electricity.id, paid.id}))
        current.apply_changes(changes, {}, builders.NOW)
        await uow.documents.update(current)
        await uow.commit()
    tagged = output(await signal.run(document))
    assert tagged["neighbours"][0]["tag_ids"] == [str(electricity.id)]
    assert tagged["known"]["tags"] == [str(electricity.id)]

    for unavailable in (
        NeighboursSignal(world.uow, world.object_store, None, embeddings),
        NeighboursSignal(world.uow, world.object_store, index, Broken()),
        NeighboursSignal(
            world.uow,
            world.object_store,
            index,
            embeddings,
            replace(NeighboursPolicy(), count=0),
        ),
    ):
        assert ABSTAINED in (await unavailable.run(document)).output

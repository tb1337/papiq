"""Contract of the classifier. Needs the fixture `classifier`."""

import pytest

from papiq.core.domain.errors import ClassifierError
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.ports import Classifier, TrainingExample

UTILITY, BANK, INSURER = (ContactId(new_id()) for _ in range(3))
INVOICE, STATEMENT = DocumentTypeId(new_id()), DocumentTypeId(new_id())
ENERGY, MONEY = TagId(new_id()), TagId(new_id())
HOME, FINANCE = TagId(new_id()), TagId(new_id())  # topics

UTILITY_TEXTS = [
    "Stadtwerke Musterstadt Stromrechnung Zählerstand Kilowattstunden Abschlag {}",
    "Stadtwerke Musterstadt Jahresabrechnung Strom Zählerstand Kilowattstunden Grundpreis {}",
    "Stadtwerke Musterstadt Abschlagsplan Strom Kilowattstunden Zählerstand Arbeitspreis {}",
]
BANK_TEXTS = [
    "Beispielbank Kontoauszug Saldo Überweisung Lastschrift Buchungstag {}",
    "Beispielbank Kontoauszug Gutschrift Saldo Buchungstag Valuta {}",
    "Beispielbank Kontoauszug Dauerauftrag Saldo Buchungstag Überweisung {}",
]


def corpus() -> list[TrainingExample]:
    utility = [
        TrainingExample(text.format(number), UTILITY, INVOICE, frozenset({ENERGY}), HOME)
        for number, text in enumerate(UTILITY_TEXTS)
    ]
    bank = [
        TrainingExample(text.format(number), BANK, STATEMENT, frozenset({MONEY}), FINANCE)
        for number, text in enumerate(BANK_TEXTS)
    ]
    return [*utility, *bank]


class ClassifierContract:
    async def test_it_learns_what_tells_the_classes_apart(self, classifier: Classifier) -> None:
        model = await classifier.train(corpus())
        prediction = await classifier.predict(
            model.data, "Stadtwerke Musterstadt: Ihr Zählerstand, Strom in Kilowattstunden"
        )
        (best, p), *_ = prediction.contacts
        assert best == UTILITY and p > 0.5
        assert prediction.document_types[0][0] == INVOICE
        assert prediction.tags[ENERGY] > prediction.tags[MONEY]
        assert prediction.known_contacts == {UTILITY, BANK}
        assert prediction.topics[0][0] == HOME
        assert prediction.known_topics == {HOME, FINANCE}

    async def test_a_topic_needs_two_examples_and_two_classes(self, classifier: Classifier) -> None:
        """As the type: documents without a topic are left out of its target."""
        examples = [
            TrainingExample(item.text, item.contact, item.document_type, item.tags, HOME)
            if item.contact == UTILITY
            else TrainingExample(item.text, item.contact, item.document_type, item.tags)
            for item in corpus()
        ]
        model = await classifier.train(examples)
        prediction = await classifier.predict(model.data, "Stadtwerke Strom Zählerstand")
        assert prediction.topics == []

    async def test_probabilities(self, classifier: Classifier) -> None:
        model = await classifier.train(corpus())
        prediction = await classifier.predict(model.data, "Beispielbank Kontoauszug Saldo")
        shares = [p for _, p in prediction.contacts]
        assert all(0 <= p <= 1 for p in shares)
        assert sum(shares) == pytest.approx(1, abs=1e-6)
        assert shares == sorted(shares, reverse=True)
        assert all(0 <= p <= 1 for p in prediction.tags.values())

    async def test_a_class_needs_two_examples_and_a_target_two_classes(
        self, classifier: Classifier
    ) -> None:
        once = TrainingExample("Nord Versicherung Beitrag Police", INSURER, INVOICE)
        examples = [*corpus(), once]
        model = await classifier.train(
            [
                TrainingExample(example.text, example.contact, INVOICE, example.tags)
                for example in examples
            ]
        )
        prediction = await classifier.predict(model.data, "Nord Versicherung Beitrag")
        assert INSURER not in prediction.known_contacts
        assert prediction.document_types == []  # one type only: nothing to tell apart

    async def test_the_model_stands_alone(self, classifier: Classifier) -> None:
        model = await classifier.train(corpus())
        assert model.data
        again = await classifier.predict(model.data, "Stadtwerke Strom Zählerstand")
        first = await classifier.predict(model.data, "Stadtwerke Strom Zählerstand")
        assert again == first

    async def test_nothing_to_learn_from(self, classifier: Classifier) -> None:
        with pytest.raises(ClassifierError):
            await classifier.train([])
        with pytest.raises(ClassifierError):
            await classifier.train([TrainingExample("", UTILITY, INVOICE)])

    async def test_a_damaged_model(self, classifier: Classifier) -> None:
        with pytest.raises(ClassifierError):
            await classifier.predict(b"no model", "Stadtwerke")

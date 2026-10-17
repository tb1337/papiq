"""The judge's rules for contact, document type, topic and tags."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from papiq.core.domain.classification import CandidateSource, FieldCheck
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId
from papiq.core.domain.master_data import Assignment, Contact, DocumentType, Tag, TagScope
from papiq.core.domain.pipeline import Outcome
from papiq.core.domain.verdict import (
    ClassifierView,
    FactsView,
    KnownLabels,
    ModelProposal,
    Neighbour,
    Signals,
    judge_contact,
    judge_tags,
    judge_topic,
    judge_type,
)

NOW = datetime(2026, 10, 13, tzinfo=UTC)
UTILITY = Contact.create(name="Stadtwerke Musterstadt", now=NOW)
BANK = Contact.create(name="Beispielbank", now=NOW)
EMPLOYEE = Contact.create(name="Max Nord", now=NOW)
CONTACTS = [UTILITY, BANK, EMPLOYEE]
INVOICE = DocumentType.create(name="Rechnung", now=NOW)
STATEMENT = DocumentType.create(name="Kontoauszug", now=NOW)
TYPES = [INVOICE, STATEMENT]
ENERGY, MONEY = Tag.create(name="Energie", now=NOW), Tag.create(name="Finanzen", now=NOW)
TAGS = [ENERGY, MONEY]


def model(**fields: object) -> ModelProposal:
    return ModelProposal(**fields)  # type: ignore[arg-type]


def classifier(
    contact: ContactId | None = None,
    p: float = 0.95,
    kind: DocumentTypeId | None = None,
    tags: dict[TagId, float] | None = None,
    *,
    contacts: frozenset[ContactId] = frozenset({UTILITY.id, BANK.id}),
    types: frozenset[DocumentTypeId] = frozenset({INVOICE.id, STATEMENT.id}),
) -> ClassifierView:
    """It knows `contacts` and `types`: by default the utility and the bank, invoices and
    statements."""
    return ClassifierView(
        contacts=[(contact, p)] if contact else [],
        document_types=[(kind, p)] if kind else [],
        tags=tags or {},
        known_contacts=contacts,
        known_types=types,
    )


def neighbours(*values: tuple[ContactId | None, DocumentTypeId | None]) -> tuple[Neighbour, ...]:
    return tuple(
        Neighbour(document=f"d{n}", contact=contact, document_type=kind, tags=frozenset())
        for n, (contact, kind) in enumerate(values)
    )


NAMED_UTILITY = FactsView(named=frozenset({UTILITY.id}))
UTILITY_IBAN = FactsView(identifiers={UTILITY.id: ("iban",)})


# --- contact ----------------------------------------------------------------------------------


def test_before_other_signals_the_model_and_the_name_in_the_text_decide() -> None:
    signals = Signals(model=model(sender="Stadtwerke Musterstadt GmbH"), facts=NAMED_UTILITY)
    check = judge_contact(signals, CONTACTS)
    assert (check.outcome, check.value) == (Outcome.OK, str(UTILITY.id))
    assert check.candidates[0].label == "Stadtwerke Musterstadt"
    assert [source.signal for source in check.candidates[0].sources] == [
        "signal_llm",
        "signal_facts",
    ]


def test_a_contact_the_text_does_not_show_needs_a_second_signal() -> None:
    alone = judge_contact(Signals(model=model(sender="Stadtwerke Musterstadt")), CONTACTS)
    assert alone.outcome is Outcome.UNCERTAIN
    assert alone.reason == "the contact 'Stadtwerke Musterstadt' does not appear in the text"
    assert alone.suggestion == str(UTILITY.id)
    # A name only similar, but the classifier is sure: green without the name in the text.
    backed = Signals(model=model(sender="Beispielbank AG Filiale"), classifier=classifier(BANK.id))
    check = judge_contact(backed, CONTACTS)
    assert (check.outcome, check.value) == (Outcome.OK, str(BANK.id))


def test_a_name_as_similar_to_two_contacts_needs_hard_evidence() -> None:
    """ "Sparkasse" is as close to both savings banks: the classifier's choice alone does not
    settle it, the name in the text does."""
    north = Contact.create(name="Sparkasse Nord", now=NOW)
    south = Contact.create(name="Sparkasse Süd", now=NOW)
    banks = [north, south]
    sure = ClassifierView(
        contacts=[(north.id, 0.95)], known_contacts=frozenset({north.id, south.id})
    )
    signals = Signals(model=model(sender="Sparkasse"), classifier=sure)
    check = judge_contact(signals, banks)
    assert (check.outcome, check.suggestion) == (Outcome.UNCERTAIN, str(north.id))
    named = replace(signals, facts=FactsView(named=frozenset({north.id})))
    assert judge_contact(named, banks).outcome is Outcome.OK


def test_similar_documents_back_a_contact_by_majority() -> None:
    three = neighbours((BANK.id, None), (BANK.id, None), (UTILITY.id, None))
    check = judge_contact(Signals(model=model(sender="Beispielbank"), neighbours=three), CONTACTS)
    assert (check.outcome, check.value) == (Outcome.OK, str(BANK.id))
    (bank, utility) = check.candidates
    voted = next(source for source in bank.sources if source.signal == "signal_neighbours")
    assert (voted.votes, voted.of, voted.names) == (2, 3, True)
    assert utility.label == "Stadtwerke Musterstadt"


def test_each_source_tells_its_value_and_from_where_it_names_one() -> None:
    """The classifier's probability and its threshold, the model's match and its threshold,
    the similar documents' votes and the highest similarity among those that carry it; and
    what the log keeps reads back the same."""
    near = (
        Neighbour(document="d0", contact=BANK.id, document_type=None, similarity=0.88),
        Neighbour(document="d1", contact=BANK.id, document_type=None, similarity=0.91),
        Neighbour(document="d2", contact=UTILITY.id, document_type=None, similarity=0.95),
    )
    signals = Signals(
        model=model(sender="Beispielbank"), classifier=classifier(BANK.id, 0.77), neighbours=near
    )
    (bank, *_) = judge_contact(signals, CONTACTS).candidates
    by = {source.signal: source for source in bank.sources}
    assert (by["signal_llm"].score, by["signal_llm"].threshold) == (1.0, 0.9)
    assert (by["signal_classifier"].score, by["signal_classifier"].threshold) == (0.77, 0.8)
    assert not by["signal_classifier"].names
    assert (by["signal_neighbours"].votes, by["signal_neighbours"].similarity) == (2, 0.91)
    for source in bank.sources:
        again = CandidateSource.from_json(source.to_json())
        assert (again.threshold, again.similarity) == (source.threshold, source.similarity)


def test_similar_documents_split_between_senders_name_none() -> None:
    """A pay slip of a new employer: the similar documents are the employee's pay slips from
    two earlier ones, alike by the form and the employee. They name no sender, so they do not
    contradict the model and the name in the text; undivided, they still would."""
    split = neighbours(*[(BANK.id, None)] * 3, *[(EMPLOYEE.id, None)] * 2)
    signals = Signals(
        model=model(sender="Stadtwerke Musterstadt"), facts=NAMED_UTILITY, neighbours=split
    )
    check = judge_contact(signals, CONTACTS)
    assert (check.outcome, check.value) == (Outcome.OK, str(UTILITY.id))
    assert check.candidates[0].value == str(UTILITY.id) and not check.candidates[0].contradicted
    undivided = neighbours(*[(BANK.id, None)] * 3, (EMPLOYEE.id, None), (None, None))
    check = judge_contact(replace(signals, neighbours=undivided), CONTACTS)
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == "the signals disagree: 'Stadtwerke Musterstadt' or 'Beispielbank'"


@pytest.mark.parametrize(
    ("signals", "reason"),
    [
        (
            Signals(
                model=model(sender="Stadtwerke Musterstadt"),
                classifier=classifier(BANK.id),
                facts=NAMED_UTILITY,
            ),
            "the signals disagree: 'Stadtwerke Musterstadt' or 'Beispielbank'",
        ),
        (
            Signals(
                model=model(sender="Stadtwerke Musterstadt", recipient="Stadtwerke Musterstadt"),
                facts=NAMED_UTILITY,
            ),
            "'Stadtwerke Musterstadt' is the recipient, not the sender",
        ),
        (
            Signals(
                model=model(sender="Stadtwerke Musterstadt"),
                facts=FactsView(named=frozenset({UTILITY.id}), identifiers={BANK.id: ("iban",)}),
            ),
            "only one signal names 'Stadtwerke Musterstadt'",
        ),
        (
            # A pay slip: the model takes the employee, an unknown name, for the sender.
            Signals(
                model=model(sender="Erika Musterfrau"),
                classifier=classifier(UTILITY.id),
                facts=UTILITY_IBAN,
            ),
            "'Erika Musterfrau' is no contact, and only other signals point to "
            "'Stadtwerke Musterstadt'",
        ),
    ],
)
def test_a_contradiction_is_never_green(signals: Signals, reason: str) -> None:
    check = judge_contact(signals, CONTACTS)
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == reason


def test_similar_documents_cover_only_what_confirmed_documents_carry() -> None:
    """Similar documents were found, but none carries a type or tag: they could not have named
    the model's, which stand on its word as before the signals. An older entry without what
    they know counts as covering everything."""
    two = neighbours((BANK.id, None), (UTILITY.id, None))
    signals = Signals(
        model=model(document_type="Rechnung", tags=("Energie",)),
        neighbours=two,
        neighbours_know=KnownLabels(contacts=frozenset({UTILITY.id, BANK.id})),
    )
    assert judge_type(signals, TYPES).outcome is Outcome.OK
    assert judge_tags(signals, TAGS).value == [str(ENERGY.id)]
    knowing = replace(
        signals,
        neighbours_know=KnownLabels(
            document_types=frozenset({INVOICE.id}), tags=frozenset({ENERGY.id})
        ),
    )
    for known in (knowing, replace(signals, neighbours_know=None)):
        assert judge_type(known, TYPES).outcome is Outcome.UNCERTAIN
        assert judge_tags(known, TAGS).suggestion == [str(ENERGY.id)]


def test_a_signal_naming_another_value_contradicts_even_one_it_does_not_know() -> None:
    """A pay slip: the model takes the employee, a contact no confirmed document has, for the
    sender. The classifier, trained on the employer's pay slips, is sure of the employer."""
    signals = Signals(
        model=model(sender="Max Nord"),
        classifier=classifier(BANK.id),
        facts=FactsView(named=frozenset({EMPLOYEE.id, BANK.id})),
    )
    check = judge_contact(signals, CONTACTS)
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == "the signals disagree: 'Max Nord' or 'Beispielbank'"


def test_classes_of_the_model_that_no_longer_exist_are_left_out() -> None:
    """Two contacts were merged after the training: the model still names the deleted one."""
    gone = Contact.create(name="Stadtwerke Alt", now=NOW)
    kind = DocumentType.create(name="Abschlag", now=NOW)
    signals = Signals(
        model=model(sender="Stadtwerke Musterstadt", document_type="Rechnung"),
        classifier=classifier(gone.id, kind=kind.id, tags={TagId(gone.id): 0.99}),
        facts=NAMED_UTILITY,
    )
    contact = judge_contact(signals, CONTACTS)
    assert (contact.outcome, contact.value) == (Outcome.OK, str(UTILITY.id))
    # The classifier knows invoices and did not name it: the model alone is not enough, but the
    # deleted type is no contradiction.
    assert judge_type(signals, TYPES).reason == "only one signal names 'Rechnung'"
    assert judge_tags(signals, TAGS).value == []


def test_an_identifier_alone_is_a_suggestion() -> None:
    check = judge_contact(Signals(facts=UTILITY_IBAN), CONTACTS)
    assert (check.outcome, check.suggestion) == (Outcome.UNCERTAIN, str(UTILITY.id))
    (candidate,) = check.candidates
    assert candidate.sources[0].identifiers == ("iban",)
    # With the classifier sure of it as well, it is green.
    sure = judge_contact(Signals(classifier=classifier(UTILITY.id), facts=UTILITY_IBAN), CONTACTS)
    assert sure.outcome is Outcome.OK


def test_an_unknown_sender_is_a_new_contact() -> None:
    check = judge_contact(Signals(model=model(sender="Neue Firma AG")), CONTACTS)
    assert (check.outcome, check.new_name) == (Outcome.UNCERTAIN, "Neue Firma AG")
    assert check.candidates[-1].value is None


# --- document type ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "outcome"),
    [
        # Before the classifier knows it, the model's type from the list is green.
        (Signals(model=model(document_type="Rechnung")), Outcome.OK),
        # The classifier knows the type but is not sure: two signals are needed.
        (
            Signals(model=model(document_type="Rechnung"), classifier=classifier()),
            Outcome.UNCERTAIN,
        ),
        (
            Signals(model=model(document_type="Rechnung"), classifier=classifier(kind=INVOICE.id)),
            Outcome.OK,
        ),
        (
            Signals(
                model=model(document_type="Rechnung"), classifier=classifier(kind=STATEMENT.id)
            ),
            Outcome.UNCERTAIN,
        ),
        # The model does not know; the classifier and the neighbours agree.
        (
            Signals(
                classifier=classifier(kind=STATEMENT.id),
                neighbours=neighbours((None, STATEMENT.id), (None, STATEMENT.id)),
            ),
            Outcome.OK,
        ),
    ],
)
def test_the_type_needs_two_signals_once_others_know_it(signals: Signals, outcome: Outcome) -> None:
    assert judge_type(signals, TYPES).outcome is outcome


def test_a_new_type_is_proposed() -> None:
    check = judge_type(Signals(model=model(new_document_type="Sammelbestätigung")), TYPES)
    assert (check.outcome, check.new_name) == (Outcome.UNCERTAIN, "Sammelbestätigung")
    assert check.reason == "no document type fits; a new type 'Sammelbestätigung' is proposed"


# --- tags -------------------------------------------------------------------------------------


def test_tags_two_signals_name_are_set_one_is_a_suggestion() -> None:
    signals = Signals(
        model=model(tags=("Energie", "Finanzen", "Unbekannt"), new_tags=("Strom",)),
        classifier=classifier(tags={ENERGY.id: 0.9, MONEY.id: 0.2}),
    )
    check = judge_tags(signals, TAGS)
    assert check.outcome is Outcome.OK
    assert check.value == [str(ENERGY.id)]
    # Ids, as the review takes them; the model's new tags stand in what it proposed.
    assert check.suggestion == [str(MONEY.id)]
    assert check.proposed == {"tags": ["Energie", "Finanzen", "Unbekannt"], "new_tags": ["Strom"]}
    # Before the classifier knows the tags, the model's are set.
    cold = judge_tags(Signals(model=model(tags=("Energie",))), TAGS)
    assert cold.value == [str(ENERGY.id)]


def test_tags_set_by_hand_are_never_proposed_and_a_child_beats_its_parent() -> None:
    """A status tag set by hand is no target even when a signal names it; set together with its
    child, a parent is left out (the child says more)."""
    paid = Tag.create(name="Bezahlt", now=NOW, assignment=Assignment.MANUAL)
    electricity = Tag.create(name="Strom", now=NOW, parent_id=ENERGY.id)
    tags = [*TAGS, paid, electricity]
    signals = Signals(
        model=model(tags=("Energie", "Strom", "Bezahlt")),
        classifier=classifier(tags={paid.id: 0.99, electricity.id: 0.95, ENERGY.id: 0.95}),
    )
    check = judge_tags(signals, tags)
    assert check.value == [str(electricity.id)]
    assert {candidate.label for candidate in check.candidates} == {"Energie", "Strom"}
    assert check.suggestion is None


# --- topic ------------------------------------------------------------------------------------

WORK = Tag.create(name="Arbeit", now=NOW, scope=TagScope.TOPIC)
HEALTH = Tag.create(name="Krankenversicherung", now=NOW, scope=TagScope.TOPIC)
PRIVATE = Tag.create(name="Privat", now=NOW, scope=TagScope.TOPIC, assignment=Assignment.MANUAL)
ELSEWHERE = Tag.create(name="Girokonto", now=NOW, scope=TagScope.TOPIC)
TOPICS = [WORK, HEALTH, PRIVATE, ELSEWHERE, *TAGS]
NORD = Contact.create(name="Nord", now=NOW, topic_ids=[WORK.id, HEALTH.id, PRIVATE.id])
GREEN = FieldCheck(field="contact", outcome=Outcome.OK, confidence=1, value=str(NORD.id))
YELLOW = FieldCheck(
    field="contact",
    outcome=Outcome.UNCERTAIN,
    confidence=0.5,
    reason="only one signal",
    suggestion=str(NORD.id),
)
NONE = FieldCheck(field="contact", outcome=Outcome.UNCERTAIN, confidence=0, reason="none")


def pair(*topics: Tag) -> FactsView:
    """Identifiers of Nord in the text, each pointing to one of `topics`."""
    return FactsView(
        identifiers={NORD.id: ("personnel_number",) * len(topics)},
        topics={NORD.id: {topic.id: ("personnel_number",) for topic in topics}},
    )


def topics_of(**p: float) -> ClassifierView:
    known = {"work": WORK, "health": HEALTH, "elsewhere": ELSEWHERE}
    return ClassifierView(
        topics=sorted(((known[name].id, value) for name, value in p.items()), key=lambda i: -i[1])
    )


def judge(signals: Signals, contact: FieldCheck = GREEN) -> FieldCheck:
    return judge_topic(signals, contact, [NORD, UTILITY], TOPICS)


@pytest.mark.parametrize(
    "signals",
    [
        # An identifier of the pair and a signal.
        Signals(model=model(topic="Arbeit"), facts=pair(WORK)),
        Signals(classifier=topics_of(work=0.9, health=0.1), facts=pair(WORK)),
        # Two signals.
        Signals(model=model(topic="arbeit"), classifier=topics_of(work=0.85)),
        Signals(
            model=model(topic="Arbeit"),
            neighbours=tuple(
                replace(item, topic=WORK.id) for item in neighbours((NORD.id, None), (None, None))
            ),
        ),
    ],
)
def test_a_topic_is_set_under_a_green_contact_by_evidence_and_a_signal_or_two_signals(
    signals: Signals,
) -> None:
    check = judge(signals)
    assert (check.outcome, check.value, check.suggestion) == (Outcome.OK, str(WORK.id), None)
    assert check.candidates[0].label == "Arbeit"


@pytest.mark.parametrize(
    ("signals", "reason"),
    [
        (Signals(model=model(topic="Arbeit")), "only one signal names 'Arbeit'"),
        (Signals(facts=pair(WORK)), "only an identifier points to 'Arbeit'"),
        (
            Signals(model=model(topic="Arbeit"), classifier=topics_of(work=0.7, health=0.3)),
            "only one signal names 'Arbeit'",
        ),
    ],
)
def test_a_topic_one_source_names_is_a_suggestion(signals: Signals, reason: str) -> None:
    check = judge(signals)
    assert (check.outcome, check.value, check.suggestion) == (Outcome.OK, None, str(WORK.id))
    assert check.reason == reason


@pytest.mark.parametrize(
    "signals",
    [
        Signals(facts=pair(WORK, HEALTH)),
        Signals(model=model(topic="Arbeit"), facts=pair(HEALTH)),
        Signals(model=model(topic="Arbeit"), classifier=topics_of(health=0.9, work=0.1)),
    ],
)
def test_topics_named_against_each_other_leave_it_empty_with_all_candidates(
    signals: Signals,
) -> None:
    check = judge(signals)
    assert (check.outcome, check.value, check.suggestion) == (Outcome.OK, None, None)
    assert {item.label for item in check.candidates} == {"Arbeit", "Krankenversicherung"}
    assert all(item.contradicted for item in check.candidates)
    assert check.reason is not None and check.reason.startswith("the signals disagree")


def test_under_a_suggested_contact_the_topic_is_only_suggested() -> None:
    signals = Signals(model=model(topic="Arbeit"), facts=pair(WORK))
    check = judge(signals, YELLOW)
    assert (check.value, check.suggestion) == (None, str(WORK.id))
    assert check.reason == "the contact 'Nord' is only suggested"
    sources = [source.signal for source in check.candidates[0].sources]
    assert sources == ["signal_llm", "signal_facts"]
    assert check.candidates[0].sources[1].identifiers == ("personnel_number",)


def test_only_the_contacts_topics_that_the_classification_sets_count() -> None:
    # Another contact's topic, one set by hand, and no contact at all.
    for signals in (
        Signals(model=model(topic="Girokonto"), classifier=topics_of(elsewhere=0.95)),
        Signals(model=model(topic="Privat")),
    ):
        check = judge(signals)
        assert (check.value, check.suggestion, check.candidates) == (None, None, ())
    check = judge(Signals(model=model(topic="Arbeit"), facts=pair(WORK)), NONE)
    assert (check.outcome, check.value, check.suggestion) == (Outcome.OK, None, None)

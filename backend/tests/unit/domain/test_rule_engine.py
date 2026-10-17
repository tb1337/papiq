from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from papiq.core.domain.documents import UNSET, Channel, Document, DocumentChanges
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.evidence import DocumentText
from papiq.core.domain.fields import FieldDefinition, FieldType, Money, Url
from papiq.core.domain.ids import (
    ContactId,
    DocumentTypeId,
    FieldId,
    SpaceId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.master_data import Tag, TagScope
from papiq.core.domain.periods import Period
from papiq.core.domain.pipeline import Outcome
from papiq.core.domain.rule_engine import (
    Effect,
    Facts,
    Match,
    Mode,
    Note,
    RulePlan,
    Situation,
    SpaceTarget,
    Taxonomy,
    Truth,
    changed_fields,
    evaluate,
    matches,
    pattern_subjects,
    pattern_text,
    plan,
)
from papiq.core.domain.rules import (
    Action,
    AddTags,
    Condition,
    ConditionField,
    ForceReview,
    Group,
    Operator,
    RemoveTags,
    Rule,
    RuleDefinition,
    RuleScope,
    RuleVersion,
    SetContact,
    SetDocumentType,
    SetField,
    SetSpace,
    SetTitle,
    SetTopic,
)
from papiq.core.domain.tag_tree import TagTree
from tests import builders
from tests.builders import NOW


def _fixed(number: int) -> UUID:
    """A fixed id, so parametrized test ids are the same in every xdist worker."""
    return UUID(f"00000000-0000-7000-8000-{number:012d}")


OWNER = UserId(_fixed(1))
STRANGER = UserId(_fixed(2))
DEFAULT = SpaceId(_fixed(3))
OWN = SpaceId(_fixed(4))  # the owner's own, not shared
SHARED = SpaceId(_fixed(5))  # others see documents in it
READ_ONLY = SpaceId(_fixed(6))
SPACES = {
    OWN: SpaceTarget("Taxes", writable=True, shared=False),
    SHARED: SpaceTarget("Family", writable=True, shared=True),
    READ_ONLY: SpaceTarget("Boss", writable=False, shared=True),
}
ACME, BETA = ContactId(_fixed(7)), ContactId(_fixed(8))
INVOICE, LETTER = DocumentTypeId(_fixed(9)), DocumentTypeId(_fixed(10))
PAID, OPEN, URGENT = TagId(_fixed(11)), TagId(_fixed(12)), TagId(_fixed(13))
EXISTING = frozenset({ACME, BETA, INVOICE, LETTER, PAID, OPEN, URGENT})
NAMES: dict[object, str] = {ACME: "ACME GmbH", BETA: "Beta AG", INVOICE: "Invoice"}


def cond(
    field: ConditionField,
    op: Operator,
    value: JsonValue = None,
    *,
    field_id: FieldId | None = None,
    case_sensitive: bool = False,
) -> Condition:
    return Condition(
        field=field, op=op, value=value, field_id=field_id, case_sensitive=case_sensitive
    )


def all_(*items: Condition | Group, negate: bool = False) -> Group:
    return Group(mode="all", items=items, negate=negate)


def any_(*items: Condition | Group, negate: bool = False) -> Group:
    return Group(mode="any", items=items, negate=negate)


IS_API = cond(ConditionField.CHANNEL, Operator.IS, "api")  # true for documents of `doc()`
IS_WEB = cond(ConditionField.CHANNEL, Operator.IS, "web")  # false for them
ALWAYS = all_(IS_API)
FROM_ACME = all_(cond(ConditionField.CONTACT, Operator.IS, str(ACME)))

_rules = 0


def rule(
    *actions: Action,
    when: Group = ALWAYS,
    priority: int = 100,
    scope: RuleScope = RuleScope.USER,
    owner: UserId | None = OWNER,
    name: str | None = None,
) -> Rule:
    """A rule; each is created a minute after the previous one."""
    global _rules
    _rules += 1
    return Rule.create(
        scope=scope,
        owner_id=None if scope is RuleScope.GLOBAL else owner,
        definition=RuleDefinition(
            name=name or f"rule {_rules}", conditions=when, actions=actions, priority=priority
        ),
        by=OWNER,
        now=NOW + timedelta(minutes=_rules),
    )


def doc(**fields: Any) -> Document:
    """A document of OWNER in their default space, from `Rechnung 2026.pdf`, via the API."""
    document = builders.document(OWNER, DEFAULT, filename="Rechnung 2026.pdf")
    for name, value in fields.items():
        setattr(document, name, value)
    return document


def situation(document: Document, mode: Mode = Mode.INGEST, **fields: Any) -> Situation:
    values: dict[str, Any] = {
        "default_space": DEFAULT,
        "definitions": {},
        "spaces": SPACES,
        "existing": EXISTING,
        "names": NAMES,
        "render": builders.TEMPLATES.render,
    }
    values.update(fields)
    return Situation(mode=mode, document=document, **values)


def run(
    rules: list[Rule], where: Situation, *, facts: Facts | None = None, before: Facts | None = None
) -> RulePlan:
    """Match `rules` against the document and plan their change."""
    found = matches(rules, facts or Facts.of(where.document), where.definitions, before=before)
    return plan(found, where)


def notes(result: RulePlan) -> list[tuple[str, str]]:
    return [(note.field, note.kind) for report in result.reports for note in report.notes]


def check(result: RulePlan, field: str) -> Any:
    return next(item for item in result.checks if item.field == field)


def check_or_none(result: RulePlan, field: str) -> Any:
    return next((item for item in result.checks if item.field == field), None)


# --- evaluation -------------------------------------------------------------------------------


def test_conditions_see_the_state_before_rules_act() -> None:
    # A adds PAID; B acts on PAID: B does not act in the same run (loop protection).
    a = rule(AddTags(frozenset({PAID})))
    b = rule(
        AddTags(frozenset({URGENT})),
        when=all_(cond(ConditionField.TAGS, Operator.CONTAINS, str(PAID))),
    )
    document = doc()

    found = matches([a, b], Facts.of(document), {})
    result = plan(found, situation(document))

    assert [item.rule for item in found] == [a]
    assert result.changes.tag_ids == frozenset({PAID})
    assert [report.rule_id for report in result.reports] == [a.id]


def test_a_rule_does_not_undo_its_own_trigger_in_the_same_run() -> None:
    # A removes OPEN when PAID is present, B adds PAID when OPEN is present: one run acts on
    # the state before, so each sees only the original tags.
    a = rule(
        RemoveTags(frozenset({OPEN})),
        when=all_(cond(ConditionField.TAGS, Operator.CONTAINS, str(PAID))),
    )
    b = rule(
        AddTags(frozenset({PAID})),
        when=all_(cond(ConditionField.TAGS, Operator.CONTAINS, str(OPEN))),
    )
    document = doc(tag_ids={OPEN})

    result = run([a, b], situation(document))

    assert result.changes.tag_ids == frozenset({OPEN, PAID})


UNKNOWN_CONTACT = cond(ConditionField.CONTACT, Operator.IS, str(ACME))


@pytest.mark.parametrize(
    ("group", "trusting", "distrusting"),
    [
        (all_(UNKNOWN_CONTACT), True, None),
        (all_(UNKNOWN_CONTACT, IS_API), True, None),
        (all_(UNKNOWN_CONTACT, IS_WEB), False, False),  # unknown and false is false
        (any_(UNKNOWN_CONTACT, IS_API), True, True),  # unknown or true is true
        (any_(UNKNOWN_CONTACT, IS_WEB), True, None),
        (all_(UNKNOWN_CONTACT, negate=True), False, None),  # not unknown is unknown
        (any_(UNKNOWN_CONTACT, IS_WEB, negate=True), False, None),
        (all_(UNKNOWN_CONTACT, IS_WEB, negate=True), True, True),
        (all_(IS_API, any_(UNKNOWN_CONTACT, negate=True)), False, None),
        (any_(IS_WEB, all_(IS_API, all_(UNKNOWN_CONTACT, negate=True))), False, None),
    ],
)
def test_three_valued_logic(group: Group, trusting: Truth, distrusting: Truth) -> None:
    facts = Facts.of(doc(contact_id=ACME), unverified=frozenset({"contact"}))

    assert evaluate(group, facts, {}) is trusting
    assert evaluate(group, facts, {}, distrust=True) is distrusting


def test_confirmed_values_are_trusted() -> None:
    facts = Facts.of(doc(contact_id=ACME, document_type_id=INVOICE))

    assert evaluate(FROM_ACME, facts, {}, distrust=True) is True


def test_unconfirmed_document_type_is_unknown() -> None:
    facts = Facts.of(doc(document_type_id=INVOICE), unverified=frozenset({"document_type"}))
    group = all_(cond(ConditionField.DOCUMENT_TYPE, Operator.PRESENT))

    assert evaluate(group, facts, {}) is True
    assert evaluate(group, facts, {}, distrust=True) is None


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        (Operator.CONTAINS, str(PAID), None),  # only the model set it
        (Operator.CONTAINS, str(OPEN), True),  # a person set it
        (Operator.CONTAINS, str(URGENT), False),
        (Operator.IN, [str(PAID), str(URGENT)], None),
        (Operator.IN, [str(PAID), str(OPEN)], True),
        (Operator.PRESENT, None, True),
        (Operator.MISSING, None, False),
    ],
)
def test_unconfirmed_tags_are_unknown(op: Operator, value: JsonValue, expected: Truth) -> None:
    facts = Facts.of(doc(tag_ids={PAID, OPEN}), unverified_tags=frozenset({PAID}))
    group = all_(cond(ConditionField.TAGS, op, value))

    assert evaluate(group, facts, {}, distrust=True) is expected


@pytest.mark.parametrize("op", [Operator.PRESENT, Operator.MISSING])
def test_only_unconfirmed_tags_make_present_and_missing_unknown(op: Operator) -> None:
    facts = Facts.of(doc(tag_ids={PAID}), unverified_tags=frozenset({PAID}))
    group = all_(cond(ConditionField.TAGS, op))

    assert evaluate(group, facts, {}) is (op is Operator.PRESENT)
    assert evaluate(group, facts, {}, distrust=True) is None


def test_facts_keep_only_unconfirmed_tags_the_document_has() -> None:
    facts = Facts.of(doc(tag_ids={PAID}), unverified_tags=frozenset({PAID, URGENT}))

    assert facts.unverified_tags == frozenset({PAID})


def test_match_says_which_unconfirmed_values_it_relies_on() -> None:
    by_contact = rule(ForceReview("x"), when=FROM_ACME)
    by_contact_or_tag = rule(
        ForceReview("x"),
        when=any_(UNKNOWN_CONTACT, cond(ConditionField.TAGS, Operator.CONTAINS, str(PAID))),
    )
    also_by_channel = rule(ForceReview("x"), when=any_(UNKNOWN_CONTACT, IS_API))
    facts = Facts.of(
        doc(contact_id=ACME, tag_ids={PAID}),
        unverified=frozenset({"contact"}),
        unverified_tags=frozenset({PAID}),
    )

    found = matches([by_contact, by_contact_or_tag, also_by_channel], facts, {})

    assert found == [
        Match(by_contact, trusted=False, distrusted=("contact",)),
        Match(by_contact_or_tag, trusted=False, distrusted=("contact", "tags")),
        Match(also_by_channel, trusted=True),
    ]


# --- text -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Invoice", True), ("invoice r-2026", True), ("R-20", False), ("Mahnung", False)],
)
def test_text_contains(value: str, expected: bool) -> None:
    facts = Facts.of(doc(), text=DocumentText("**Invoice** R-2026 from ACME"))
    group = all_(cond(ConditionField.TEXT, Operator.CONTAINS, value))

    assert evaluate(group, facts, {}) is expected


def test_text_conditions_without_text_do_not_hold() -> None:
    facts = Facts.of(doc(), patterns={("text", "R-\\d+", False): True})

    assert evaluate(all_(cond(ConditionField.TEXT, Operator.CONTAINS, "R")), facts, {}) is False
    assert evaluate(all_(cond(ConditionField.TEXT, Operator.MATCHES, "R-\\d+")), facts, {}) is False


def test_text_matches_reads_the_pattern_results() -> None:
    facts = Facts.of(
        doc(),
        text=DocumentText("Invoice R-2026"),
        patterns={("text", "R-\\d+", True): True, ("text", "^X", False): False},
    )

    def holds(pattern: str, case_sensitive: bool = False) -> Truth:
        condition = cond(
            ConditionField.TEXT, Operator.MATCHES, pattern, case_sensitive=case_sensitive
        )
        return evaluate(all_(condition), facts, {})

    assert holds("R-\\d+", case_sensitive=True) is True
    assert holds("R-\\d+") is False  # another case setting: no result, no match (time out)
    assert holds("^X") is False
    assert holds("Invoice") is False  # not computed


# --- fields -------------------------------------------------------------------------------


def field(data_type: FieldType, *choices: str) -> FieldDefinition:
    return FieldDefinition.create(
        name=data_type.value, data_type=data_type, now=NOW, choices=choices
    )


AMOUNT = field(FieldType.AMOUNT)
DUE = field(FieldType.DATE)
REFERENCE = field(FieldType.TEXT)
INTERVAL = field(FieldType.CHOICE, "monthly", "yearly", "weekly")
COUNT = field(FieldType.NUMBER)
PAID_FLAG = field(FieldType.BOOLEAN)
PORTAL = field(FieldType.LINK)
SPAN = field(FieldType.PERIOD)
DEFINITIONS = {
    item.id: item for item in (AMOUNT, DUE, REFERENCE, INTERVAL, COUNT, PAID_FLAG, PORTAL, SPAN)
}

FIELD_FACTS = Facts.of(
    doc(
        fields={
            AMOUNT.id: Money(Decimal("120.00"), "EUR"),
            DUE.id: date(2026, 3, 31),
            REFERENCE.id: "ACME Contract 7",
            INTERVAL.id: "monthly",
            COUNT.id: Decimal("12.50"),
            PAID_FLAG.id: False,
            PORTAL.id: Url("https://portal.example.org/Invoices"),
            SPAN.id: Period.month(2026, 9),
        }
    ),
    patterns={(str(REFERENCE.id), "^ACME", True): True},
)


@pytest.mark.parametrize(
    ("attr", "op", "value", "expected"),
    [
        (AMOUNT, Operator.GT, {"amount": "100", "currency": "EUR"}, True),
        (AMOUNT, Operator.GT, {"amount": "120", "currency": "EUR"}, False),
        (AMOUNT, Operator.LT, {"amount": "200", "currency": "EUR"}, True),
        (AMOUNT, Operator.IS, {"amount": "120", "currency": "EUR"}, True),
        (AMOUNT, Operator.GT, {"amount": "100", "currency": "USD"}, False),  # other currency
        (AMOUNT, Operator.LT, {"amount": "200", "currency": "USD"}, False),
        (AMOUNT, Operator.IS, {"amount": "120", "currency": "USD"}, False),
        (DUE, Operator.GT, "2026-03-30", True),
        (DUE, Operator.GT, "2026-03-31", False),
        (DUE, Operator.LT, "2026-04-01", True),
        (DUE, Operator.IS, "2026-03-31", True),
        (REFERENCE, Operator.IS, "acme contract 7", True),  # text ignores case
        (REFERENCE, Operator.IS, "ACME Contract", False),
        (REFERENCE, Operator.IN, ["x", "ACME CONTRACT 7"], True),
        (REFERENCE, Operator.CONTAINS, "contract", True),
        (REFERENCE, Operator.MATCHES, "^ACME", False),  # result for case_sensitive only
        (INTERVAL, Operator.IN, ["monthly", "yearly"], True),
        (INTERVAL, Operator.IN, ["weekly", "yearly"], False),
        (INTERVAL, Operator.IS, "monthly", True),
        (COUNT, Operator.IS, "12.5", True),
        (COUNT, Operator.GT, "12", True),
        (COUNT, Operator.LT, "12", False),
        (PAID_FLAG, Operator.IS, False, True),
        (PAID_FLAG, Operator.IS, True, False),
        (PORTAL, Operator.IS, "https://portal.example.org/Invoices", True),
        (PORTAL, Operator.CONTAINS, "invoices", True),
        (AMOUNT, Operator.PRESENT, None, True),
        (AMOUNT, Operator.MISSING, None, False),
        (SPAN, Operator.IS, {"start": "2026-09-01", "end": "2026-09-30"}, True),
        (SPAN, Operator.IS, {"start": "2026-09-01", "end": "2026-09-29"}, False),
        (SPAN, Operator.OVERLAPS, {"start": "2026-09-30", "end": "2026-10-31"}, True),
        (SPAN, Operator.OVERLAPS, {"start": "2026-01-01", "end": "2026-12-31"}, True),
        (SPAN, Operator.OVERLAPS, {"start": "2026-10-01", "end": "2026-10-31"}, False),
        (SPAN, Operator.GT, "2026-08-31", True),  # starts after
        (SPAN, Operator.GT, "2026-09-01", False),
        (SPAN, Operator.LT, "2026-10-01", True),  # ends before
        (SPAN, Operator.LT, "2026-09-30", False),
        (SPAN, Operator.PRESENT, None, True),
    ],
)
def test_field_conditions(
    attr: FieldDefinition, op: Operator, value: JsonValue, expected: bool
) -> None:
    group = all_(cond(ConditionField.FIELD, op, value, field_id=attr.id))

    assert evaluate(group, FIELD_FACTS, DEFINITIONS) is expected


def test_field_matches_reads_the_pattern_results() -> None:
    group = all_(
        cond(
            ConditionField.FIELD,
            Operator.MATCHES,
            "^ACME",
            field_id=REFERENCE.id,
            case_sensitive=True,
        )
    )

    assert evaluate(group, FIELD_FACTS, DEFINITIONS) is True


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        (Operator.GT, {"amount": "1", "currency": "EUR"}, False),
        (Operator.IS, {"amount": "1", "currency": "EUR"}, False),
        (Operator.PRESENT, None, False),
        (Operator.MISSING, None, True),
    ],
)
def test_field_without_value(op: Operator, value: JsonValue, expected: bool) -> None:
    group = all_(cond(ConditionField.FIELD, op, value, field_id=AMOUNT.id))

    assert evaluate(group, Facts.of(doc()), DEFINITIONS) is expected


def test_condition_on_a_deleted_field_does_not_hold() -> None:
    group = all_(cond(ConditionField.FIELD, Operator.MISSING, field_id=AMOUNT.id))

    assert evaluate(group, Facts.of(doc()), {}) is False


def test_field_value_that_no_longer_fits_does_not_hold() -> None:
    # The choice was removed from the definition after the rule was written.
    group = all_(cond(ConditionField.FIELD, Operator.IS, "daily", field_id=INTERVAL.id))

    assert evaluate(group, FIELD_FACTS, DEFINITIONS) is False


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        (Operator.IS, "2026-03-31", True),
        (Operator.GT, "2026-03-31", False),
        (Operator.LT, "2026-04-01", True),
        (Operator.PRESENT, None, True),
    ],
)
def test_document_date(op: Operator, value: JsonValue, expected: bool) -> None:
    group = all_(cond(ConditionField.DOCUMENT_DATE, op, value))

    assert evaluate(group, Facts.of(doc(document_date=date(2026, 3, 31))), {}) is expected
    assert evaluate(group, Facts.of(doc()), {}) is False


def test_contact_and_channel() -> None:
    facts = Facts.of(doc(contact_id=ACME, channel=Channel.WEB))

    def holds(condition: Condition) -> Truth:
        return evaluate(all_(condition), facts, {})

    assert holds(cond(ConditionField.CONTACT, Operator.IN, [str(BETA), str(ACME)])) is True
    assert holds(cond(ConditionField.CONTACT, Operator.IS, str(BETA))) is False
    assert holds(cond(ConditionField.DOCUMENT_TYPE, Operator.MISSING)) is True
    assert holds(cond(ConditionField.CHANNEL, Operator.IS, "web")) is True
    assert holds(cond(ConditionField.CHANNEL, Operator.IN, ["api", "migration"])) is False


def test_contact_ids_compare_as_ids_not_as_text() -> None:
    # `_uuid` accepts any spelling of an id (upper case, without hyphens); the condition must
    # match the contact however the id was written, as tag conditions do.
    facts = Facts.of(doc(contact_id=ACME))

    assert evaluate(all_(cond(ConditionField.CONTACT, Operator.IS, str(ACME).upper())), facts, {})


# --- combining actions ------------------------------------------------------------------------


def test_same_value_from_two_rules_is_set() -> None:
    a, b = rule(SetContact(ACME)), rule(SetContact(ACME), priority=500)

    result = run([a, b], situation(doc()))

    assert result.changes.contact_id == ACME
    assert [report.applied for report in result.reports] == [
        [Effect("contact", None, str(ACME))],
        [Effect("contact", None, str(ACME))],
    ]
    assert check(result, "contact").outcome is Outcome.OK
    assert not result.uncertain


def test_different_values_conflict_whatever_the_priority() -> None:
    high, low = rule(SetContact(ACME), priority=900), rule(SetContact(BETA), priority=10)

    result = run([low, high], situation(doc()))

    assert result.changes.contact_id is UNSET
    assert notes(result) == [("contact", "conflict"), ("contact", "conflict")]
    contact = check(result, "contact")
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(ACME)
    assert result.uncertain
    assert result.reasons[0].startswith("contact: rules set different values")


def test_reasons_name_values_not_ids() -> None:
    """Contacts, types, topics, spaces, tags and fields by their names, a field's value as
    written; the ids stay in the suggestion and the effects."""
    names = {**NAMES, PAID: "Paid"}
    contacts = run(
        [rule(SetContact(ACME), name="Nord, Arbeit"), rule(SetContact(BETA), name="Süd")],
        situation(doc(), names=names),
    )
    assert check(contacts, "contact").reason == (
        "rules set different values ('Nord, Arbeit': 'ACME GmbH', 'Süd': 'Beta AG')"
    )
    assert check(contacts, "contact").suggestion == str(ACME)
    amounts = run(
        [
            rule(SetField(AMOUNT.id, {"amount": "12.50", "currency": "EUR"}), name="a"),
            rule(SetField(AMOUNT.id, {"amount": "13", "currency": "EUR"}), name="b"),
            rule(SetField(INTERVAL.id, "monthly"), name="c"),
            rule(SetField(INTERVAL.id, "yearly"), name="d"),
            rule(SetField(SPAN.id, {"start": "2026-09-01", "end": "2026-09-30"}), name="e"),
            rule(SetField(SPAN.id, {"start": "2026-07-01", "end": "2026-09-30"}), name="f"),
        ],
        situation(doc(), definitions=DEFINITIONS),
    )
    assert amounts.reasons == [
        "'amount': rules set different values ('a': 12.50 EUR, 'b': 13 EUR)",
        "'choice': rules set different values ('c': 'monthly', 'd': 'yearly')",
        "'period': rules set different values ('e': 2026-09, 'f': 2026-Q3)",
    ]
    model = run(
        [rule(SetContact(ACME))],
        situation(doc(contact_id=BETA), names=names, model_values={"contact": str(BETA)}),
    )
    assert check(model, "contact").reason == "rules: the model set another value ('Beta AG')"
    tags = run(
        [rule(AddTags(frozenset({PAID}))), rule(RemoveTags(frozenset({PAID})))],
        situation(doc(), names=names),
    )
    assert check(tags, "tags").reason == "tag 'Paid' is added and removed by rules"
    refused = run([rule(SetSpace(READ_ONLY))], situation(doc()))
    assert check(refused, "space").reason.endswith(
        f"no write access to space '{SPACES[READ_ONLY].name}' (any more)"
    )


def test_person_decided_field_overrules_the_rule() -> None:
    result = run(
        [rule(SetContact(ACME))],
        situation(doc(contact_id=BETA), locked=frozenset({"contact"})),
    )

    assert result.changes.contact_id is UNSET
    assert notes(result) == [("contact", "overruled")]
    assert result.checks == []
    assert not result.uncertain


def test_person_decided_field_is_not_yellow_even_if_rules_disagree() -> None:
    result = run(
        [rule(SetContact(ACME)), rule(SetContact(BETA))],
        situation(doc(contact_id=BETA), locked=frozenset({"contact"})),
    )

    assert notes(result) == [("contact", "conflict"), ("contact", "conflict")]
    assert not result.uncertain


def test_rule_does_not_replace_an_unconfirmed_model_value() -> None:
    result = run(
        [rule(SetDocumentType(LETTER))],
        situation(doc(document_type_id=INVOICE), model_values={"document_type": str(INVOICE)}),
    )

    assert result.changes.document_type_id is UNSET
    assert notes(result) == [("document_type", "conflict")]
    document_type = check(result, "document_type")
    assert document_type.outcome is Outcome.UNCERTAIN
    assert document_type.suggestion == str(LETTER)


def test_rule_agreeing_with_the_model_changes_nothing() -> None:
    result = run(
        [rule(SetDocumentType(INVOICE))],
        situation(doc(document_type_id=INVOICE), model_values={"document_type": str(INVOICE)}),
    )

    assert result.changes == DocumentChanges()
    assert notes(result) == [] and result.checks == []


def test_action_on_deleted_master_data_is_skipped() -> None:
    gone = ContactId(new_id())

    result = run([rule(SetContact(gone), AddTags(frozenset({TagId(new_id())})))], situation(doc()))

    assert result.changes == DocumentChanges()
    assert notes(result) == [("contact", "skipped"), ("tags", "skipped")]


# --- tags -------------------------------------------------------------------------------------


def test_tags_of_several_rules_are_combined() -> None:
    result = run(
        [
            rule(AddTags(frozenset({PAID}))),
            rule(AddTags(frozenset({URGENT})), RemoveTags(frozenset({OPEN}))),
        ],
        situation(doc(tag_ids={OPEN})),
    )

    assert result.changes.tag_ids == frozenset({PAID, URGENT})
    assert not result.uncertain


def test_tag_added_and_removed_is_a_conflict() -> None:
    adds, removes = rule(AddTags(frozenset({PAID, URGENT}))), rule(RemoveTags(frozenset({PAID})))

    result = run([adds, removes], situation(doc()))

    assert result.changes.tag_ids == frozenset({URGENT})
    assert notes(result) == [("tags", "conflict"), ("tags", "conflict")]
    assert check(result, "tags").outcome is Outcome.UNCERTAIN


def test_removing_a_model_tag_is_no_conflict() -> None:
    result = run(
        [rule(RemoveTags(frozenset({PAID})))],
        situation(doc(tag_ids={PAID, OPEN})),
    )

    assert result.changes.tag_ids == frozenset({OPEN})
    assert result.reports[0].applied == [Effect("tags", str(PAID), None)]
    assert not result.uncertain


def test_tags_a_person_set_or_removed_are_kept() -> None:
    result = run(
        [rule(AddTags(frozenset({URGENT})), RemoveTags(frozenset({OPEN})))],
        situation(
            doc(tag_ids={OPEN}),
            person_added_tags=frozenset({OPEN}),
            person_removed_tags=frozenset({URGENT}),
        ),
    )

    assert result.changes.tag_ids is UNSET
    assert notes(result) == [("tags", "overruled"), ("tags", "overruled")]
    assert not result.uncertain


def test_tags_already_as_wanted_change_nothing() -> None:
    result = run(
        [rule(AddTags(frozenset({PAID})), RemoveTags(frozenset({OPEN})))],
        situation(doc(tag_ids={PAID})),
    )

    assert result.changes.tag_ids is UNSET
    assert result.effects == []


# --- spaces and distrust ---------------------------------------------------------------------


def distrusted(document: Document) -> Facts:
    return Facts.of(document, unverified=frozenset({"contact"}))


def test_unconfirmed_contact_does_not_file_into_a_shared_space() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetSpace(SHARED), when=FROM_ACME)], situation(document), facts=distrusted(document)
    )

    assert result.space is None
    assert notes(result) == [("space", "refused")]
    assert "only with a confirmed contact" in result.reports[0].notes[0].reason
    contact = check(result, "contact")
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(ACME)
    assert result.uncertain


def test_unconfirmed_contact_may_file_into_the_owners_own_space() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetSpace(OWN), when=FROM_ACME)], situation(document), facts=distrusted(document)
    )

    assert result.space == OWN
    assert result.reports[0].applied == [Effect("space", str(DEFAULT), str(OWN))]
    assert not result.uncertain


def test_confirmed_contact_files_into_a_shared_space() -> None:
    result = run([rule(SetSpace(SHARED), when=FROM_ACME)], situation(doc(contact_id=ACME)))

    assert result.space == SHARED


def test_unconfirmed_contact_on_change_is_reported_only() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetSpace(SHARED), when=FROM_ACME)],
        situation(document, Mode.CHANGE),
        facts=distrusted(document),
    )

    assert result.space is None
    assert notes(result) == [("space", "refused")]
    assert not result.uncertain


def test_retroactive_filing_on_unconfirmed_values_is_a_conflict() -> None:
    """Applied to existing documents, filing into a shared space on an unconfirmed contact is
    a conflict: it acts only where the person accepted it."""
    document = doc(contact_id=ACME)
    rules = [rule(SetSpace(SHARED), when=FROM_ACME)]

    refused = run(rules, situation(document, Mode.RETROACTIVE), facts=distrusted(document))
    accepted = run(
        rules,
        situation(document, Mode.RETROACTIVE, accept_conflicts=True),
        facts=distrusted(document),
    )

    assert refused.space is None
    assert notes(refused) == [("space", "conflict")]
    assert accepted.space == SHARED


def test_no_write_access_to_the_space() -> None:
    ingest = run([rule(SetSpace(READ_ONLY))], situation(doc()))
    change = run([rule(SetSpace(READ_ONLY))], situation(doc(), Mode.CHANGE))

    assert ingest.space is None and change.space is None
    assert notes(ingest) == notes(change) == [("space", "refused")]
    assert check(ingest, "space").outcome is Outcome.UNCERTAIN
    assert not change.uncertain


def test_space_of_a_rule_of_another_user_is_refused() -> None:
    result = run([rule(SetSpace(OWN), owner=STRANGER)], situation(doc()))

    assert result.space is None
    assert notes(result) == [("space", "refused")]


def test_deleted_space_is_skipped() -> None:
    result = run([rule(SetSpace(SpaceId(new_id())))], situation(doc()))

    assert result.space is None
    assert notes(result) == [("space", "skipped")]
    assert not result.uncertain


def test_rules_filing_into_different_spaces_conflict() -> None:
    result = run([rule(SetSpace(OWN)), rule(SetSpace(SHARED))], situation(doc()))

    assert result.space is None
    assert check(result, "space").outcome is Outcome.UNCERTAIN


# --- global rules -----------------------------------------------------------------------------


def global_rule_storing(*actions: Action) -> Rule:
    """A global rule whose stored version has actions the scope check forbids (defence in
    depth: the engine refuses them too)."""
    found = rule(AddTags(frozenset({PAID})), scope=RuleScope.GLOBAL)
    found.current = RuleVersion(
        rule_id=found.id,
        number=2,
        definition=RuleDefinition(name="global", conditions=ALWAYS, actions=actions),
        created_at=NOW,
        created_by=None,
    )
    return found


def test_global_rule_cannot_change_space_or_visibility() -> None:
    result = run(
        [
            global_rule_storing(
                SetSpace(OWN),
                SetContact(ACME),
                SetDocumentType(INVOICE),
                SetTitle("{contact}"),
                AddTags(frozenset({PAID})),
            )
        ],
        situation(doc(contact_id=ACME)),
    )

    assert result.space is None
    assert result.changes == DocumentChanges(tag_ids=frozenset({PAID}))
    assert sorted(notes(result)) == [
        ("contact", "refused"),
        ("document_type", "refused"),
        ("space", "refused"),
        ("title", "refused"),
    ]


def test_global_rule_tags_sets_fields_and_forces_reviews() -> None:
    result = run(
        [
            rule(
                AddTags(frozenset({PAID})),
                SetField(PAID_FLAG.id, True),
                ForceReview("check"),
                scope=RuleScope.GLOBAL,
                name="Paid",
            )
        ],
        situation(doc(), definitions=DEFINITIONS),
    )

    assert result.changes.tag_ids == frozenset({PAID})
    assert result.changes.fields == {PAID_FLAG.id: True}
    assert result.reviews == ["rule 'Paid': check"]


# --- order ------------------------------------------------------------------------------------


def test_reports_in_rule_order() -> None:
    low = rule(ForceReview("x"), priority=50)
    user_older = rule(ForceReview("x"))
    user_newer = rule(ForceReview("x"))
    global_newest = rule(ForceReview("x"), scope=RuleScope.GLOBAL)
    high = rule(ForceReview("x"), priority=900)

    result = run([low, user_newer, global_newest, high, user_older], situation(doc()))

    assert [report.rule_id for report in result.reports] == [
        item.id for item in (high, global_newest, user_older, user_newer, low)
    ]


# --- modes ------------------------------------------------------------------------------------


def test_forced_review_on_arrival() -> None:
    result = run([rule(ForceReview("check the IBAN"), name="Bank")], situation(doc()))

    assert result.reviews == ["rule 'Bank': check the IBAN"]
    assert result.uncertain
    assert result.reasons == ["review: rule 'Bank': check the IBAN", "rule 'Bank': check the IBAN"]


def test_forced_review_after_a_person_confirmed_it() -> None:
    confirmed, other = rule(ForceReview("check")), rule(ForceReview("new"))

    result = run([confirmed, other], situation(doc(), reviewed=frozenset({confirmed.id})))

    assert result.reviews == [f"rule '{other.definition.name}': new"]
    assert notes(result) == [("review", "overruled"), ("review", "review")]
    assert result.uncertain


@pytest.mark.parametrize("mode", [Mode.CHANGE, Mode.RETROACTIVE])
def test_forced_review_only_on_arrival(mode: Mode) -> None:
    result = run([rule(ForceReview("check"))], situation(doc(), mode))

    assert result.reviews == []
    assert notes(result) == [("review", "skipped")]
    assert not result.uncertain


def test_change_acts_on_the_edge_only() -> None:
    becomes_true = rule(AddTags(frozenset({PAID})), when=FROM_ACME)
    was_true = rule(AddTags(frozenset({URGENT})))
    before, after = doc(), doc(contact_id=ACME)

    found = matches([becomes_true, was_true], Facts.of(after), {}, before=Facts.of(before))
    again = matches([becomes_true], Facts.of(after), {}, before=Facts.of(after))

    assert [item.rule for item in found] == [becomes_true]
    assert again == []


def test_change_turns_nothing_yellow() -> None:
    document = doc(document_type_id=INVOICE)

    result = run(
        [
            rule(SetContact(ACME)),
            rule(SetContact(BETA)),
            rule(SetDocumentType(LETTER)),
            rule(AddTags(frozenset({PAID}))),
            rule(RemoveTags(frozenset({PAID}))),
            rule(SetTitle("{{ contact | default: '' }} letter")),
        ],
        situation(document, Mode.CHANGE, model_values={"document_type": str(INVOICE)}),
    )

    assert result.changes.contact_id is UNSET
    assert result.changes.document_type_id is UNSET
    assert result.changes.tag_ids is UNSET
    assert result.changes.title == "letter"
    assert ("contact", "conflict") in notes(result)
    assert ("document_type", "conflict") in notes(result)
    assert ("tags", "conflict") in notes(result)
    assert result.checks == []
    assert not result.uncertain


def test_retroactive_conflict_with_an_existing_value() -> None:
    accepted = run(
        [rule(SetContact(ACME))],
        situation(doc(contact_id=BETA), Mode.RETROACTIVE, accept_conflicts=True),
    )
    refused = run([rule(SetContact(ACME))], situation(doc(contact_id=BETA), Mode.RETROACTIVE))
    empty = run([rule(SetContact(ACME))], situation(doc(), Mode.RETROACTIVE))

    assert accepted.changes.contact_id == ACME
    assert refused.changes.contact_id is UNSET
    assert notes(refused) == [("contact", "conflict")]
    assert "not accepted" in refused.reports[0].notes[0].reason
    assert empty.changes.contact_id == ACME
    assert not refused.uncertain and refused.checks == []


def test_retroactive_default_space_and_file_name_title_count_as_empty() -> None:
    actions = (SetSpace(OWN), SetTitle("{{ contact }} invoice"))
    fresh = run([rule(*actions)], situation(doc(contact_id=ACME), Mode.RETROACTIVE))
    filed = run(
        [rule(*actions)],
        situation(doc(contact_id=ACME, space_id=SHARED, title="Mine"), Mode.RETROACTIVE),
    )

    assert fresh.space == OWN
    assert fresh.changes.title == "ACME GmbH invoice"
    assert filed.space is None and filed.changes.title is UNSET
    assert notes(filed) == [("space", "conflict"), ("title", "conflict")]


def test_retroactive_the_machines_title_counts_as_empty() -> None:
    titled = doc(contact_id=ACME, title="Stromrechnung März 2026")
    rules = [rule(SetTitle("{{ contact }} invoice"))]
    replaced = run(rules, situation(titled, Mode.RETROACTIVE, machine_title=titled.title))
    kept = run(rules, situation(titled, Mode.RETROACTIVE, machine_title="Another"))
    assert replaced.changes.title == "ACME GmbH invoice"
    assert kept.changes.title is UNSET


# --- titles -----------------------------------------------------------------------------------


def test_title_template() -> None:
    template = (
        "{{ contact }} {{ document_type }} {{ document_date | date: '%Y-%m' }} ({{ filename }})"
    )
    result = run(
        [rule(SetDocumentType(INVOICE)), rule(SetTitle(template))],
        situation(doc(contact_id=ACME, document_date=date(2026, 3, 31))),
    )

    assert result.changes.title == "ACME GmbH Invoice 2026-03 (Rechnung 2026)"
    assert check(result, "title").outcome is Outcome.OK


def test_title_uses_the_contact_a_rule_sets() -> None:
    result = run([rule(SetContact(BETA), SetTitle("Letter {{ contact }}"))], situation(doc()))

    assert result.changes.title == "Letter Beta AG"


def test_a_missing_value_leaves_the_title() -> None:
    """Written without a test or default, a missing value is no title: the one there stays."""
    result = run([rule(SetTitle("Invoice {{ contact }}"))], situation(doc()))

    assert result.changes.title is UNSET
    assert result.reports[0].notes == [Note("title", "skipped", "contact has no value")]
    assert check_or_none(result, "title") is None  # nothing to decide: not yellow


def test_optional_parts_and_whitespace() -> None:
    template = (
        "{{ contact | default: '' }}  Invoice   "
        "{% if document_date %}{{ document_date }}{% endif %}"
    )
    result = run([rule(SetTitle(template))], situation(doc()))

    assert result.changes.title == "Invoice"


def test_empty_title_is_skipped() -> None:
    result = run([rule(SetTitle("{% if contact %}{{ contact }}{% endif %}"))], situation(doc()))

    assert result.changes.title is UNSET
    assert notes(result) == [("title", "skipped")]


def test_the_title_sees_fields_tags_space_and_owner() -> None:
    template = "{{ field.amount }} {{ tags | join: '+' }} {{ space }} {{ owner }}"
    document = doc(fields={AMOUNT.id: Money(Decimal("99.90"), "EUR")}, tag_ids={PAID})
    names = {**NAMES, PAID: "paid", URGENT: "urgent", DEFAULT: "Inbox"}
    result = run(
        [rule(AddTags(frozenset({URGENT})), SetTitle(template))],
        situation(document, definitions=DEFINITIONS, names=names, owner_name="tobi"),
    )

    assert result.changes.title == "99,90 EUR paid+urgent Inbox tobi"


def test_a_text_field_from_a_template() -> None:
    number = FieldDefinition.create(name="Reference", data_type=FieldType.TEXT, now=NOW)
    result = run(
        [rule(SetContact(ACME), SetField(number.id, "{{ contact | upcase }}-{{ filename }}"))],
        situation(doc(), definitions={number.id: number}),
    )

    assert result.changes.fields == {number.id: "ACME GMBH-Rechnung 2026"}


def test_a_review_holds_where_its_reason_cannot_be_rendered() -> None:
    result = run(
        [
            rule(ForceReview("check {{ contact }}"), name="a"),
            rule(ForceReview("from {{ filename }}"), name="b"),
        ],
        situation(doc()),
    )

    assert result.reviews == ["rule 'a': check {{ contact }}", "rule 'b': from Rechnung 2026"]


def title_pattern(template: str) -> Rule:
    """The rule of the title pattern of INVOICE."""
    return Rule.create(
        scope=RuleScope.GLOBAL,
        owner_id=None,
        definition=RuleDefinition(name="Invoice", conditions=ALWAYS, actions=(SetTitle(template),)),
        by=OWNER,
        now=NOW,
        managed_by=INVOICE,
    )


def test_a_type_s_title_pattern_sets_the_title_as_a_global_rule() -> None:
    result = run(
        [title_pattern("{{ document_type }} {{ filename }}")],
        situation(doc(document_type_id=INVOICE)),
    )

    assert result.changes.title == "Invoice Rechnung 2026"
    # Any other global rule still cannot.
    other = run([global_rule_storing(SetTitle("x"))], situation(doc()))
    assert notes(other) == [("title", "refused")]


def test_the_pattern_gives_way_to_another_rule_and_to_a_person() -> None:
    rules = [title_pattern("{{ document_type }}"), rule(SetTitle("Mine"))]
    result = run(rules, situation(doc(document_type_id=INVOICE)))

    assert result.changes.title == "Mine"
    assert ("title", "yielded") in notes(result)
    assert check(result, "title").outcome is Outcome.OK  # no conflict

    person = run(rules[:1], situation(doc(document_type_id=INVOICE), locked=frozenset({"title"})))
    assert person.changes.title is UNSET
    assert notes(person) == [("title", "overruled")]


def test_a_title_pattern_rule_is_global_and_may_only_title() -> None:
    with pytest.raises(ValidationError, match="global"):
        Rule.create(
            scope=RuleScope.USER,
            owner_id=OWNER,
            definition=RuleDefinition(name="x", conditions=ALWAYS, actions=(SetTitle("x"),)),
            by=OWNER,
            now=NOW,
            managed_by=INVOICE,
        )
    with pytest.raises(ValidationError, match="cannot set_contact"):
        Rule.create(
            scope=RuleScope.GLOBAL,
            owner_id=None,
            definition=RuleDefinition(name="x", conditions=ALWAYS, actions=(SetContact(ACME),)),
            by=OWNER,
            now=NOW,
            managed_by=INVOICE,
        )


def test_titles_of_two_rules_conflict() -> None:
    result = run([rule(SetTitle("A")), rule(SetTitle("B"))], situation(doc()))

    assert result.changes.title is UNSET
    assert check(result, "title").outcome is Outcome.UNCERTAIN


# --- fields -------------------------------------------------------------------------------


def test_set_field() -> None:
    value: JsonValue = {"amount": "99.90", "currency": "EUR"}

    result = run([rule(SetField(AMOUNT.id, value))], situation(doc(), definitions=DEFINITIONS))

    assert result.changes.fields == {AMOUNT.id: Money(Decimal("99.90"), "EUR")}
    assert result.reports[0].applied == [Effect(f"field:{AMOUNT.id}", None, value)]


def test_field_for_another_document_type_is_skipped() -> None:
    bound = FieldDefinition.create(
        name="invoice number",
        data_type=FieldType.TEXT,
        now=NOW,
        document_type_ids=[INVOICE],
    )
    definitions = {bound.id: bound}

    letter = run(
        [rule(SetField(bound.id, "R-1"))],
        situation(doc(document_type_id=LETTER), definitions=definitions),
    )
    becomes_invoice = run(
        [rule(SetDocumentType(INVOICE), SetField(bound.id, "R-1"))],
        situation(doc(document_type_id=LETTER), definitions=definitions),
    )

    assert letter.changes.fields == {}
    assert notes(letter) == [(f"field:{bound.id}", "skipped")]
    assert becomes_invoice.changes.fields == {bound.id: "R-1"}


def test_field_value_that_no_longer_fits_is_skipped() -> None:
    result = run(
        [rule(SetField(INTERVAL.id, "daily")), rule(SetField(FieldId(new_id()), "x"))],
        situation(doc(), definitions=DEFINITIONS),
    )

    assert result.changes.fields == {}
    assert [kind for _, kind in notes(result)] == ["skipped", "skipped"]


# --- helpers ----------------------------------------------------------------------------------


def test_pattern_subjects() -> None:
    by_text = rule(
        ForceReview("x"),
        when=all_(cond(ConditionField.TEXT, Operator.MATCHES, "R-\\d+", case_sensitive=True)),
    )
    by_field = rule(
        ForceReview("x"),
        when=any_(
            cond(ConditionField.FIELD, Operator.MATCHES, "^A", field_id=REFERENCE.id),
            cond(ConditionField.FIELD, Operator.MATCHES, "^h", field_id=PORTAL.id),
            cond(ConditionField.FIELD, Operator.MATCHES, "^x", field_id=DUE.id),
        ),
    )
    document = doc(fields={REFERENCE.id: "ACME 7", PORTAL.id: Url("https://example.org")})

    assert pattern_subjects([by_text, by_field], document, "Invoice R-1") == {
        ("text", "R-\\d+", True): "Invoice R-1",
        (str(REFERENCE.id), "^A", False): "ACME 7",
        (str(PORTAL.id), "^h", False): "https://example.org",
    }
    assert ("text", "R-\\d+", True) not in pattern_subjects([by_text], document, None)


def test_pattern_text() -> None:
    assert pattern_text("# **Invoice**\n\n| R-1 | `x` |", 100) == "Invoice R-1 x"
    assert pattern_text("abc def", 5) == "abc d"


def test_changed_fields() -> None:
    attr = FieldId(new_id())

    changes = DocumentChanges(title="x", contact_id=None, fields={attr: None})

    assert changed_fields(changes) == {"title", "contact", f"field:{attr}"}
    assert changed_fields(DocumentChanges()) == frozenset()


def test_plan_to_json() -> None:
    contact_rule = rule(SetContact(ACME), name="ACME")

    data = run([contact_rule], situation(doc())).to_json()

    assert data["rules"] == [
        {
            "rule_id": str(contact_rule.id),
            "version": 1,
            "scope": "user",
            "applied": [{"field": "contact", "old": None, "new": str(ACME)}],
            "notes": [],
            "name": "ACME",
        }
    ]
    assert data["reviews"] == []
    assert isinstance(data["fields"], list) and len(data["fields"]) == 1


# --- tags in trees and groups -------------------------------------------------------------


def _tagged() -> tuple[dict[str, Tag], Taxonomy]:
    """Insurance with Health (with Dental); the contact group Insurer with Health insurer,
    whose member is ACME; the type group Bills, whose member is INVOICE."""
    insurance = Tag.create(name="Insurance", now=NOW)
    health = Tag.create(name="Health", now=NOW, parent_id=insurance.id)
    dental = Tag.create(name="Dental", now=NOW, parent_id=health.id)
    insurer = Tag.create(name="Insurer", now=NOW, scope=TagScope.CONTACT)
    health_insurer = Tag.create(
        name="Health insurer", now=NOW, scope=TagScope.CONTACT, parent_id=insurer.id
    )
    bills = Tag.create(name="Bills", now=NOW, scope=TagScope.DOCUMENT_TYPE)
    tags = {
        "insurance": insurance,
        "health": health,
        "dental": dental,
        "insurer": insurer,
        "health_insurer": health_insurer,
        "bills": bills,
    }
    groups = {ACME: frozenset({health_insurer.id}), INVOICE: frozenset({bills.id})}
    by_id: dict[UUID, frozenset[TagId]] = dict(groups)
    return tags, Taxonomy(TagTree.of(tags.values()), by_id)


def test_a_tag_condition_matches_the_tags_below_it() -> None:
    tags, taxonomy = _tagged()
    facts = Facts.of(doc(tag_ids={tags["dental"].id}), taxonomy=taxonomy)
    for wanted, expected in (("insurance", True), ("health", True), ("dental", True)):
        condition = cond(ConditionField.TAGS, Operator.CONTAINS, str(tags[wanted].id))
        assert evaluate(all_(condition), facts, {}) is expected
    on_health = cond(ConditionField.TAGS, Operator.CONTAINS, str(tags["health"].id))
    assert (
        evaluate(
            all_(on_health), Facts.of(doc(tag_ids={tags["insurance"].id}), taxonomy=taxonomy), {}
        )
        is False
    )
    one_of = cond(ConditionField.TAGS, Operator.IN, [str(PAID), str(tags["insurance"].id)])
    assert evaluate(all_(one_of), facts, {}) is True


def test_conditions_on_the_groups_of_contact_and_type() -> None:
    tags, taxonomy = _tagged()
    facts = Facts.of(doc(contact_id=ACME, document_type_id=INVOICE), taxonomy=taxonomy)
    other = Facts.of(doc(contact_id=BETA), taxonomy=taxonomy)

    def truth(field: ConditionField, op: Operator, value: JsonValue, of: Facts) -> Truth:
        return evaluate(all_(cond(field, op, value)), of, {})

    group, kind = ConditionField.CONTACT_GROUP, ConditionField.DOCUMENT_TYPE_GROUP
    insurer, bills = str(tags["insurer"].id), str(tags["bills"].id)
    assert truth(group, Operator.CONTAINS, insurer, facts) is True  # a group below it
    assert truth(group, Operator.CONTAINS, str(tags["health_insurer"].id), facts) is True
    assert truth(group, Operator.IN, [bills, insurer], facts) is True
    assert truth(group, Operator.CONTAINS, insurer, other) is False
    assert truth(group, Operator.PRESENT, None, facts) is True
    assert truth(group, Operator.MISSING, None, other) is True
    assert truth(kind, Operator.CONTAINS, bills, facts) is True
    assert truth(kind, Operator.PRESENT, None, other) is False


def test_groups_of_an_unconfirmed_contact_are_unknown_under_distrust() -> None:
    tags, taxonomy = _tagged()
    document = doc(contact_id=ACME, document_type_id=INVOICE)
    facts = Facts.of(document, unverified=frozenset({"contact"}), taxonomy=taxonomy)
    in_group = all_(cond(ConditionField.CONTACT_GROUP, Operator.CONTAINS, str(tags["insurer"].id)))
    of_type = all_(cond(ConditionField.DOCUMENT_TYPE_GROUP, Operator.PRESENT))
    assert evaluate(in_group, facts, {}) is True
    assert evaluate(in_group, facts, {}, distrust=True) is None
    assert evaluate(of_type, facts, {}, distrust=True) is True  # the type is confirmed
    filing = rule(SetSpace(SHARED), when=in_group)
    (match,) = matches([filing], facts, {})
    assert (match.trusted, match.distrusted) == (False, ("contact",))
    result = plan([match], situation(document))
    assert ("space", "refused") in notes(result)


def test_an_unconfirmed_tag_below_the_wanted_one_is_unknown_under_distrust() -> None:
    tags, taxonomy = _tagged()
    facts = Facts.of(
        doc(tag_ids={tags["dental"].id}),
        unverified_tags=frozenset({tags["dental"].id}),
        taxonomy=taxonomy,
    )
    wanted = all_(cond(ConditionField.TAGS, Operator.CONTAINS, str(tags["insurance"].id)))
    assert evaluate(wanted, facts, {}, distrust=True) is None


def test_rules_add_document_tags_only_and_leave_the_parent_of_a_child() -> None:
    tags, taxonomy = _tagged()
    insurance, health, dental = (tags[name].id for name in ("insurance", "health", "dental"))
    existing = EXISTING | {tag.id for tag in tags.values()}
    names: dict[object, str] = {**NAMES, **{tag.id: tag.name for tag in tags.values()}}
    where = situation(doc(tag_ids={health}), existing=existing, names=names, taxonomy=taxonomy)
    parent = rule(AddTags(frozenset({insurance})))
    group = rule(AddTags(frozenset({tags["insurer"].id})))
    result = run([parent, group], where)
    assert result.changes.tag_ids is UNSET  # the document keeps Health: Insurance says less
    assert ("tags", "skipped") in notes(result)
    assert not result.effects
    # A child set by a rule replaces its parent.
    child = rule(AddTags(frozenset({dental, PAID})))
    result = run([child], where)
    assert result.changes.tag_ids == frozenset({dental, PAID})


def test_templates_see_the_groups_of_contact_and_type() -> None:
    tags, taxonomy = _tagged()
    names: dict[object, str] = {**NAMES, **{tag.id: tag.name for tag in tags.values()}}
    where = situation(doc(document_type_id=INVOICE), names=names, taxonomy=taxonomy)
    title = SetTitle("{{ contact_groups | join: ', ' }} / {{ document_type_groups | join }}")
    result = run([rule(SetContact(ACME), title)], where)
    assert result.changes.title == "Health insurer / Bills"


# --- topics -------------------------------------------------------------------------------------


def _topics() -> tuple[dict[str, Tag], Taxonomy]:
    """Health below Insurance, and Work; ACME enables Health and Work, BETA Work."""
    insurance = Tag.create(name="Insurance", now=NOW, scope=TagScope.TOPIC)
    health = Tag.create(name="Health", now=NOW, scope=TagScope.TOPIC, parent_id=insurance.id)
    work = Tag.create(name="Work", now=NOW, scope=TagScope.TOPIC)
    tags = {"insurance": insurance, "health": health, "work": work}
    topics: dict[UUID, frozenset[TagId]] = {
        ACME: frozenset({health.id, work.id}),
        BETA: frozenset({work.id}),
    }
    return tags, Taxonomy(TagTree.of(tags.values()), topics=topics)


def test_a_topic_condition_matches_the_topics_below_it_and_is_unknown_unconfirmed() -> None:
    tags, taxonomy = _topics()
    document = doc(contact_id=ACME, topic_id=tags["health"].id)
    facts = Facts.of(document, taxonomy=taxonomy)

    def truth(op: Operator, value: JsonValue, of: Facts = facts) -> Truth:
        return evaluate(all_(cond(ConditionField.TOPIC, op, value)), of, {}, distrust=True)

    insurance, health, work = (str(tags[name].id) for name in ("insurance", "health", "work"))
    assert truth(Operator.IS, insurance) is True
    assert truth(Operator.IS, health) is True
    assert truth(Operator.IS, work) is False
    assert truth(Operator.IN, [work, insurance]) is True
    assert truth(Operator.PRESENT, None) is True
    assert truth(Operator.MISSING, None, Facts.of(doc(), taxonomy=taxonomy)) is True
    unconfirmed = Facts.of(document, unverified=frozenset({"topic"}), taxonomy=taxonomy)
    assert truth(Operator.IS, health, unconfirmed) is None
    filing = rule(SetSpace(SHARED), when=all_(cond(ConditionField.TOPIC, Operator.IS, health)))
    (match,) = matches([filing], unconfirmed, {})
    assert (match.trusted, match.distrusted) == (False, ("topic",))


def test_a_rule_sets_a_topic_the_contact_enables() -> None:
    tags, taxonomy = _topics()
    health, work, insurance = tags["health"].id, tags["work"].id, tags["insurance"].id
    existing = EXISTING | {tag.id for tag in tags.values()}
    names: dict[object, str] = {**NAMES, **{tag.id: tag.name for tag in tags.values()}}

    def where(document: Document, mode: Mode = Mode.INGEST, **fields: Any) -> Situation:
        return situation(
            document, mode, existing=existing, names=names, taxonomy=taxonomy, **fields
        )

    result = run([rule(SetTopic(health), SetTitle("{{ topic }}"))], where(doc(contact_id=ACME)))
    assert (result.changes.topic_id, result.changes.title) == (health, "Health")
    assert check(result, "topic").value == str(health)
    # One the contact does not enable is skipped and noted, also without a contact.
    for document in (doc(contact_id=ACME), doc()):
        result = run([rule(SetTopic(insurance))], where(document))
        assert result.changes.topic_id is UNSET
        assert notes(result) == [("topic", "skipped")]
    skipped = run([rule(SetTopic(insurance))], where(doc(contact_id=ACME)))
    assert skipped.reports[0].notes[0].reason == (
        "its contact does not enable the topic 'Insurance'"
    )
    # Global rules cannot set it.
    with pytest.raises(ValidationError, match="set_topic"):
        rule(SetTopic(work), scope=RuleScope.GLOBAL)
    # The contact a rule sets decides; two rules with different topics conflict.
    result = run([rule(SetContact(BETA), SetTopic(work))], where(doc(contact_id=ACME)))
    assert (result.changes.contact_id, result.changes.topic_id) == (BETA, work)
    result = run(
        [rule(SetTopic(work), name="a"), rule(SetTopic(health), name="b")],
        where(doc(contact_id=ACME)),
    )
    assert result.changes.topic_id is UNSET
    assert check(result, "topic").outcome is Outcome.UNCERTAIN
    assert (
        check(result, "topic").reason == "rules set different values ('a': 'Work', 'b': 'Health')"
    )
    # A person's topic wins; the model's is not replaced silently.
    person = run([rule(SetTopic(work))], where(doc(contact_id=ACME), locked=frozenset({"topic"})))
    assert (person.changes.topic_id, notes(person)) == (UNSET, [("topic", "overruled")])
    model = where(doc(contact_id=ACME, topic_id=health), model_values={"topic": str(health)})
    result = run([rule(SetTopic(work))], model)
    assert result.changes.topic_id is UNSET
    assert check(result, "topic").outcome is Outcome.UNCERTAIN


def test_a_contact_a_rule_sets_clears_a_topic_it_does_not_enable() -> None:
    tags, taxonomy = _topics()
    health, work = tags["health"].id, tags["work"].id
    where = situation(
        doc(contact_id=ACME, topic_id=health),
        existing=EXISTING | {health, work},
        taxonomy=taxonomy,
    )
    result = run([rule(SetContact(BETA))], where)
    assert (result.changes.contact_id, result.changes.topic_id) == (BETA, None)
    assert Effect("topic", str(health), None) in result.effects
    kept = situation(doc(contact_id=ACME, topic_id=work), existing=EXISTING, taxonomy=taxonomy)
    result = run([rule(SetContact(BETA))], kept)
    assert (result.changes.contact_id, result.changes.topic_id) == (BETA, UNSET)


def test_changed_fields_name_the_topic() -> None:
    assert changed_fields(DocumentChanges(topic_id=None)) == {"topic"}

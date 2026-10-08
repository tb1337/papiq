from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.documents import UNSET, Channel, Document, DocumentChanges
from papiq.core.domain.evidence import DocumentText
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import Outcome
from papiq.core.domain.rule_engine import (
    DrawerTarget,
    Effect,
    Facts,
    Match,
    Mode,
    RulePlan,
    Situation,
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
    SetAttribute,
    SetContact,
    SetDocumentType,
    SetDrawer,
    SetTitle,
)
from tests import builders
from tests.builders import NOW


def _fixed(number: int) -> UUID:
    """A fixed id, so parametrized test ids are the same in every xdist worker."""
    return UUID(f"00000000-0000-7000-8000-{number:012d}")


OWNER = UserId(_fixed(1))
STRANGER = UserId(_fixed(2))
DEFAULT = DrawerId(_fixed(3))
OWN = DrawerId(_fixed(4))  # the owner's own, not shared
SHARED = DrawerId(_fixed(5))  # others see documents in it
READ_ONLY = DrawerId(_fixed(6))
DRAWERS = {
    OWN: DrawerTarget("Taxes", writable=True, shared=False),
    SHARED: DrawerTarget("Family", writable=True, shared=True),
    READ_ONLY: DrawerTarget("Boss", writable=False, shared=True),
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
    attribute_id: AttributeId | None = None,
    case_sensitive: bool = False,
) -> Condition:
    return Condition(
        field=field, op=op, value=value, attribute_id=attribute_id, case_sensitive=case_sensitive
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
    """A document of OWNER in their default drawer, from `Rechnung 2026.pdf`, via the API."""
    document = builders.document(OWNER, DEFAULT, filename="Rechnung 2026.pdf")
    for name, value in fields.items():
        setattr(document, name, value)
    return document


def situation(document: Document, mode: Mode = Mode.INGEST, **fields: Any) -> Situation:
    values: dict[str, Any] = {
        "default_drawer": DEFAULT,
        "definitions": {},
        "drawers": DRAWERS,
        "existing": EXISTING,
        "names": NAMES,
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


# --- attributes -------------------------------------------------------------------------------


def attribute(data_type: AttributeType, *choices: str) -> AttributeDefinition:
    return AttributeDefinition.create(
        name=data_type.value, data_type=data_type, now=NOW, choices=choices
    )


AMOUNT = attribute(AttributeType.AMOUNT)
DUE = attribute(AttributeType.DATE)
REFERENCE = attribute(AttributeType.TEXT)
INTERVAL = attribute(AttributeType.CHOICE, "monthly", "yearly", "weekly")
COUNT = attribute(AttributeType.NUMBER)
PAID_FLAG = attribute(AttributeType.BOOLEAN)
PORTAL = attribute(AttributeType.LINK)
DEFINITIONS = {
    item.id: item for item in (AMOUNT, DUE, REFERENCE, INTERVAL, COUNT, PAID_FLAG, PORTAL)
}

ATTRIBUTE_FACTS = Facts.of(
    doc(
        attributes={
            AMOUNT.id: Money(Decimal("120.00"), "EUR"),
            DUE.id: date(2026, 3, 31),
            REFERENCE.id: "ACME Contract 7",
            INTERVAL.id: "monthly",
            COUNT.id: Decimal("12.50"),
            PAID_FLAG.id: False,
            PORTAL.id: Url("https://portal.example.org/Invoices"),
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
    ],
)
def test_attribute_conditions(
    attr: AttributeDefinition, op: Operator, value: JsonValue, expected: bool
) -> None:
    group = all_(cond(ConditionField.ATTRIBUTE, op, value, attribute_id=attr.id))

    assert evaluate(group, ATTRIBUTE_FACTS, DEFINITIONS) is expected


def test_attribute_matches_reads_the_pattern_results() -> None:
    group = all_(
        cond(
            ConditionField.ATTRIBUTE,
            Operator.MATCHES,
            "^ACME",
            attribute_id=REFERENCE.id,
            case_sensitive=True,
        )
    )

    assert evaluate(group, ATTRIBUTE_FACTS, DEFINITIONS) is True


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        (Operator.GT, {"amount": "1", "currency": "EUR"}, False),
        (Operator.IS, {"amount": "1", "currency": "EUR"}, False),
        (Operator.PRESENT, None, False),
        (Operator.MISSING, None, True),
    ],
)
def test_attribute_without_value(op: Operator, value: JsonValue, expected: bool) -> None:
    group = all_(cond(ConditionField.ATTRIBUTE, op, value, attribute_id=AMOUNT.id))

    assert evaluate(group, Facts.of(doc()), DEFINITIONS) is expected


def test_condition_on_a_deleted_attribute_does_not_hold() -> None:
    group = all_(cond(ConditionField.ATTRIBUTE, Operator.MISSING, attribute_id=AMOUNT.id))

    assert evaluate(group, Facts.of(doc()), {}) is False


def test_attribute_value_that_no_longer_fits_does_not_hold() -> None:
    # The choice was removed from the definition after the rule was written.
    group = all_(cond(ConditionField.ATTRIBUTE, Operator.IS, "daily", attribute_id=INTERVAL.id))

    assert evaluate(group, ATTRIBUTE_FACTS, DEFINITIONS) is False


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


# --- drawers and distrust ---------------------------------------------------------------------


def distrusted(document: Document) -> Facts:
    return Facts.of(document, unverified=frozenset({"contact"}))


def test_unconfirmed_contact_does_not_file_into_a_shared_drawer() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetDrawer(SHARED), when=FROM_ACME)], situation(document), facts=distrusted(document)
    )

    assert result.drawer is None
    assert notes(result) == [("drawer", "refused")]
    assert "only with a confirmed contact" in result.reports[0].notes[0].reason
    contact = check(result, "contact")
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(ACME)
    assert result.uncertain


def test_unconfirmed_contact_may_file_into_the_owners_own_drawer() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetDrawer(OWN), when=FROM_ACME)], situation(document), facts=distrusted(document)
    )

    assert result.drawer == OWN
    assert result.reports[0].applied == [Effect("drawer", str(DEFAULT), str(OWN))]
    assert not result.uncertain


def test_confirmed_contact_files_into_a_shared_drawer() -> None:
    result = run([rule(SetDrawer(SHARED), when=FROM_ACME)], situation(doc(contact_id=ACME)))

    assert result.drawer == SHARED


def test_unconfirmed_contact_on_change_is_reported_only() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetDrawer(SHARED), when=FROM_ACME)],
        situation(document, Mode.CHANGE),
        facts=distrusted(document),
    )

    assert result.drawer is None
    assert notes(result) == [("drawer", "refused")]
    assert not result.uncertain


def test_retroactive_application_trusts_the_persons_selection() -> None:
    document = doc(contact_id=ACME)

    result = run(
        [rule(SetDrawer(SHARED), when=FROM_ACME)],
        situation(document, Mode.RETROACTIVE),
        facts=distrusted(document),
    )

    assert result.drawer == SHARED


def test_no_write_access_to_the_drawer() -> None:
    ingest = run([rule(SetDrawer(READ_ONLY))], situation(doc()))
    change = run([rule(SetDrawer(READ_ONLY))], situation(doc(), Mode.CHANGE))

    assert ingest.drawer is None and change.drawer is None
    assert notes(ingest) == notes(change) == [("drawer", "refused")]
    assert check(ingest, "drawer").outcome is Outcome.UNCERTAIN
    assert not change.uncertain


def test_drawer_of_a_rule_of_another_user_is_refused() -> None:
    result = run([rule(SetDrawer(OWN), owner=STRANGER)], situation(doc()))

    assert result.drawer is None
    assert notes(result) == [("drawer", "refused")]


def test_deleted_drawer_is_skipped() -> None:
    result = run([rule(SetDrawer(DrawerId(new_id())))], situation(doc()))

    assert result.drawer is None
    assert notes(result) == [("drawer", "skipped")]
    assert not result.uncertain


def test_rules_filing_into_different_drawers_conflict() -> None:
    result = run([rule(SetDrawer(OWN)), rule(SetDrawer(SHARED))], situation(doc()))

    assert result.drawer is None
    assert check(result, "drawer").outcome is Outcome.UNCERTAIN


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


def test_global_rule_cannot_change_drawer_or_visibility() -> None:
    result = run(
        [
            global_rule_storing(
                SetDrawer(OWN),
                SetContact(ACME),
                SetDocumentType(INVOICE),
                SetTitle("{contact}"),
                AddTags(frozenset({PAID})),
            )
        ],
        situation(doc(contact_id=ACME)),
    )

    assert result.drawer is None
    assert result.changes == DocumentChanges(tag_ids=frozenset({PAID}))
    assert sorted(notes(result)) == [
        ("contact", "refused"),
        ("document_type", "refused"),
        ("drawer", "refused"),
        ("title", "refused"),
    ]


def test_global_rule_tags_sets_attributes_and_forces_reviews() -> None:
    result = run(
        [
            rule(
                AddTags(frozenset({PAID})),
                SetAttribute(PAID_FLAG.id, True),
                ForceReview("check"),
                scope=RuleScope.GLOBAL,
                name="Paid",
            )
        ],
        situation(doc(), definitions=DEFINITIONS),
    )

    assert result.changes.tag_ids == frozenset({PAID})
    assert result.changes.attributes == {PAID_FLAG.id: True}
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


def test_forced_review_after_a_person_confirmed() -> None:
    result = run([rule(ForceReview("check"))], situation(doc(), review_confirmed=True))

    assert result.reviews == []
    assert notes(result) == [("review", "overruled")]
    assert not result.uncertain


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
            rule(SetTitle("{contact} letter")),
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


def test_retroactive_default_drawer_and_file_name_title_count_as_empty() -> None:
    actions = (SetDrawer(OWN), SetTitle("{contact} invoice"))
    fresh = run([rule(*actions)], situation(doc(contact_id=ACME), Mode.RETROACTIVE))
    filed = run(
        [rule(*actions)],
        situation(doc(contact_id=ACME, drawer_id=SHARED, title="Mine"), Mode.RETROACTIVE),
    )

    assert fresh.drawer == OWN
    assert fresh.changes.title == "ACME GmbH invoice"
    assert filed.drawer is None and filed.changes.title is UNSET
    assert notes(filed) == [("drawer", "conflict"), ("title", "conflict")]


# --- titles -----------------------------------------------------------------------------------


def test_title_placeholders() -> None:
    result = run(
        [
            rule(SetDocumentType(INVOICE)),
            rule(SetTitle("{contact} {document_type} {document_date} ({filename})")),
        ],
        situation(doc(contact_id=ACME, document_date=date(2026, 3, 31))),
    )

    assert result.changes.title == "ACME GmbH Invoice 2026-03-31 (Rechnung 2026)"
    assert check(result, "title").outcome is Outcome.OK


def test_title_uses_the_contact_a_rule_sets() -> None:
    result = run([rule(SetContact(BETA), SetTitle("Letter {contact}"))], situation(doc()))

    assert result.changes.title == "Letter Beta AG"


def test_unknown_values_render_empty() -> None:
    result = run([rule(SetTitle("{contact}  Invoice   {document_date}"))], situation(doc()))

    assert result.changes.title == "Invoice"


def test_empty_title_is_skipped() -> None:
    result = run([rule(SetTitle("{contact} {document_type}"))], situation(doc()))

    assert result.changes.title is UNSET
    assert notes(result) == [("title", "skipped")]


def test_titles_of_two_rules_conflict() -> None:
    result = run([rule(SetTitle("A")), rule(SetTitle("B"))], situation(doc()))

    assert result.changes.title is UNSET
    assert check(result, "title").outcome is Outcome.UNCERTAIN


# --- attributes -------------------------------------------------------------------------------


def test_set_attribute() -> None:
    value: JsonValue = {"amount": "99.90", "currency": "EUR"}

    result = run([rule(SetAttribute(AMOUNT.id, value))], situation(doc(), definitions=DEFINITIONS))

    assert result.changes.attributes == {AMOUNT.id: Money(Decimal("99.90"), "EUR")}
    assert result.reports[0].applied == [Effect(f"attribute:{AMOUNT.id}", None, value)]


def test_attribute_for_another_document_type_is_skipped() -> None:
    bound = AttributeDefinition.create(
        name="invoice number",
        data_type=AttributeType.TEXT,
        now=NOW,
        document_type_ids=[INVOICE],
    )
    definitions = {bound.id: bound}

    letter = run(
        [rule(SetAttribute(bound.id, "R-1"))],
        situation(doc(document_type_id=LETTER), definitions=definitions),
    )
    becomes_invoice = run(
        [rule(SetDocumentType(INVOICE), SetAttribute(bound.id, "R-1"))],
        situation(doc(document_type_id=LETTER), definitions=definitions),
    )

    assert letter.changes.attributes == {}
    assert notes(letter) == [(f"attribute:{bound.id}", "skipped")]
    assert becomes_invoice.changes.attributes == {bound.id: "R-1"}


def test_attribute_value_that_no_longer_fits_is_skipped() -> None:
    result = run(
        [rule(SetAttribute(INTERVAL.id, "daily")), rule(SetAttribute(AttributeId(new_id()), "x"))],
        situation(doc(), definitions=DEFINITIONS),
    )

    assert result.changes.attributes == {}
    assert [kind for _, kind in notes(result)] == ["skipped", "skipped"]


# --- helpers ----------------------------------------------------------------------------------


def test_pattern_subjects() -> None:
    by_text = rule(
        ForceReview("x"),
        when=all_(cond(ConditionField.TEXT, Operator.MATCHES, "R-\\d+", case_sensitive=True)),
    )
    by_attribute = rule(
        ForceReview("x"),
        when=any_(
            cond(ConditionField.ATTRIBUTE, Operator.MATCHES, "^A", attribute_id=REFERENCE.id),
            cond(ConditionField.ATTRIBUTE, Operator.MATCHES, "^h", attribute_id=PORTAL.id),
            cond(ConditionField.ATTRIBUTE, Operator.MATCHES, "^x", attribute_id=DUE.id),
        ),
    )
    document = doc(attributes={REFERENCE.id: "ACME 7", PORTAL.id: Url("https://example.org")})

    assert pattern_subjects([by_text, by_attribute], document, "Invoice R-1") == {
        ("text", "R-\\d+", True): "Invoice R-1",
        (str(REFERENCE.id), "^A", False): "ACME 7",
        (str(PORTAL.id), "^h", False): "https://example.org",
    }
    assert ("text", "R-\\d+", True) not in pattern_subjects([by_text], document, None)


def test_pattern_text() -> None:
    assert pattern_text("# **Invoice**\n\n| R-1 | `x` |", 100) == "Invoice R-1 x"
    assert pattern_text("abc def", 5) == "abc d"


def test_changed_fields() -> None:
    attr = AttributeId(new_id())

    changes = DocumentChanges(title="x", contact_id=None, attributes={attr: None})

    assert changed_fields(changes) == {"title", "contact", f"attribute:{attr}"}
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

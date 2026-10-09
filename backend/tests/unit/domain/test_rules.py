import json
from datetime import timedelta
from uuid import UUID

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    FieldId,
    RuleId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.rules import (
    GLOBAL_ACTIONS,
    MAX_ACTIONS,
    MAX_CONDITIONS,
    MAX_DEPTH,
    MAX_LIST,
    MAX_NAME,
    MAX_PATTERN,
    MAX_TEXT,
    Action,
    AddTags,
    ApplicationStatus,
    Condition,
    ConditionField,
    ForceReview,
    Group,
    Operator,
    References,
    RemoveTags,
    Rule,
    RuleApplication,
    RuleDefinition,
    RuleScope,
    RuleVersion,
    SetContact,
    SetDocumentType,
    SetDrawer,
    SetField,
    SetTitle,
    Trigger,
    action_from_json,
    check_fields,
    check_scope,
    condition_from_json,
    condition_to_json,
    definition_from_json,
    definition_to_json,
    group_from_json,
)
from tests.builders import NOW

OWNER = UserId(new_id())
ADMIN = UserId(new_id())
CONTACT = ContactId(new_id())
OTHER_CONTACT = ContactId(new_id())
TYPE = DocumentTypeId(new_id())
TAG = TagId(new_id())
OTHER_TAG = TagId(new_id())
DRAWER = DrawerId(new_id())
FIELD = FieldId(new_id())
ID = str(new_id())

ALWAYS = Condition(field=ConditionField.CHANNEL, op=Operator.IN, value=["web", "api"])


def group(*items: Condition | Group, mode: str = "all") -> Group:
    return Group(mode="all" if mode == "all" else "any", items=items)


def definition(
    *actions: Action, conditions: Group | None = None, name: str = "Invoices"
) -> RuleDefinition:
    return RuleDefinition(
        name=name,
        conditions=conditions or group(ALWAYS),
        actions=actions or (AddTags(frozenset({TAG})),),
    )


def field(data_type: FieldType, choices: tuple[str, ...] = ()) -> FieldDefinition:
    return FieldDefinition.create(
        name=data_type.value, data_type=data_type, now=NOW, choices=choices
    )


# --- JSON -------------------------------------------------------------------------------------


def full_definition() -> RuleDefinition:
    return RuleDefinition(
        name="Everything",
        priority=250,
        triggers=frozenset({Trigger.INGEST}),
        conditions=Group(
            mode="all",
            items=(
                Condition(field=ConditionField.CONTACT, op=Operator.IS, value=str(CONTACT)),
                Condition(field=ConditionField.DOCUMENT_TYPE, op=Operator.IN, value=[str(TYPE)]),
                Condition(field=ConditionField.TAGS, op=Operator.CONTAINS, value=str(TAG)),
                Condition(field=ConditionField.TAGS, op=Operator.MISSING),
                Condition(field=ConditionField.CHANNEL, op=Operator.IN, value=["web", "api"]),
                Condition(field=ConditionField.DOCUMENT_DATE, op=Operator.GT, value="2026-01-01"),
                Condition(
                    field=ConditionField.TEXT,
                    op=Operator.MATCHES,
                    value=r"Rechnung\s+Nr",
                    case_sensitive=True,
                ),
                Group(
                    mode="any",
                    negate=True,
                    items=(
                        Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value="Mahnung"),
                        Group(
                            mode="all",
                            items=(
                                Condition(
                                    field=ConditionField.FIELD,
                                    op=Operator.GT,
                                    value={"amount": "100.00", "currency": "EUR"},
                                    field_id=FIELD,
                                ),
                                Condition(
                                    field=ConditionField.FIELD,
                                    op=Operator.PRESENT,
                                    field_id=FIELD,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        actions=(
            SetDrawer(DRAWER),
            SetContact(CONTACT),
            SetDocumentType(TYPE),
            SetTitle("{contact} {document_date}"),
            AddTags(frozenset({TAG, OTHER_TAG})),
            RemoveTags(frozenset({OTHER_TAG})),
            SetField(FIELD, {"amount": "12.50", "currency": "EUR"}),
            ForceReview("check the amount"),
        ),
    )


def test_definition_json_round_trip() -> None:
    original = full_definition()

    data = json.loads(json.dumps(definition_to_json(original)))

    assert definition_from_json(data) == original


def test_definition_json_shape() -> None:
    data = definition_to_json(full_definition())

    assert data["name"] == "Everything"
    assert data["priority"] == 250
    assert data["triggers"] == ["ingest"]
    conditions = data["conditions"]
    assert isinstance(conditions, dict) and set(conditions) == {"all"}
    items = conditions["all"]
    assert isinstance(items, list)
    assert items[3] == {"field": "tags", "op": "missing"}  # no value key
    assert items[6] == {
        "field": "text",
        "op": "matches",
        "value": r"Rechnung\s+Nr",
        "case_sensitive": True,
    }
    negated = items[7]
    assert isinstance(negated, dict) and negated["not"] is True and "any" in negated
    inner = negated["any"]
    assert isinstance(inner, list) and inner[0] == {
        "field": "text",
        "op": "contains",
        "value": "Mahnung",
    }
    assert {"field": "field", "op": "present", "field_id": str(FIELD)} in (
        inner[1]["all"] if isinstance(inner[1], dict) and isinstance(inner[1]["all"], list) else []
    )
    actions = data["actions"]
    assert isinstance(actions, list)
    assert [action["type"] for action in actions if isinstance(action, dict)] == [
        "set_drawer",
        "set_contact",
        "set_document_type",
        "set_title",
        "add_tags",
        "remove_tags",
        "set_field",
        "force_review",
    ]
    assert actions[4] == {"type": "add_tags", "tag_ids": sorted([str(TAG), str(OTHER_TAG)])}


def test_definition_json_defaults() -> None:
    parsed = definition_from_json(
        {
            "name": "Defaults",
            "conditions": {"any": [{"field": "channel", "op": "is", "value": "web"}]},
            "actions": [{"type": "force_review", "reason": "look"}],
        }
    )

    assert parsed.priority == 100
    assert parsed.triggers == frozenset(Trigger)
    assert parsed.conditions == Group(
        mode="any",
        items=(Condition(field=ConditionField.CHANNEL, op=Operator.IS, value="web"),),
    )


CHANNEL_JSON: JsonObject = {"field": "channel", "op": "is", "value": "web"}
VALID_RULE: JsonObject = {
    "name": "R",
    "conditions": {"all": [CHANNEL_JSON]},
    "actions": [{"type": "force_review", "reason": "look"}],
}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"owner": "x"}, "rule: unknown key 'owner'"),
        ({"name": None}, "name: expected text"),
        ({"priority": "high"}, "priority: expected a whole number"),
        ({"priority": 1.5}, "priority: expected a whole number"),
        ({"triggers": "ingest"}, "triggers: expected a list"),
        ({"triggers": ["upload"]}, "triggers: unknown value 'upload'"),
        ({"actions": {"type": "force_review"}}, "actions: expected a list"),
        ({"actions": [{"type": "delete"}]}, r"actions\[0\].type: unknown action 'delete'"),
        (
            {"actions": [{"type": "set_contact", "contact_id": "nope"}]},
            r"actions\[0\]: contact_id: 'nope' is no id",
        ),
        (
            {"actions": [{"type": "add_tags", "tag_ids": str(TAG)}]},
            r"actions\[0\]: tag_ids: expected a list",
        ),
        (
            {"actions": [{"type": "set_title", "template": "x", "extra": 1}]},
            r"actions\[0\]: unknown key 'extra'",
        ),
        ({"conditions": []}, "conditions: expected an object"),
        (
            {"conditions": {"all": [CHANNEL_JSON], "any": [CHANNEL_JSON]}},
            "conditions: a group has exactly one of 'all' and 'any'",
        ),
        ({"conditions": {"all": []}}, "conditions: a group needs at least one condition"),
        ({"conditions": {"all": "x"}}, r"conditions.all: expected a list"),
        (
            {"conditions": {"all": [CHANNEL_JSON], "not": 1}},
            "conditions.not: expected true or false",
        ),
        (
            {"conditions": {"all": [{"any": [{"field": "text", "op": "is", "value": "x"}]}]}},
            r"conditions.all\[0\].any\[0\]: operator 'is' does not apply to text",
        ),
    ],
)
def test_definition_json_errors_name_the_place(change: JsonObject, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        definition_from_json({**VALID_RULE, **change})


def test_definition_from_json_needs_an_object() -> None:
    with pytest.raises(ValidationError, match="rule: expected an object"):
        definition_from_json([])


# --- conditions -------------------------------------------------------------------------------


VALID_CONDITIONS: list[JsonObject] = [
    {"field": "contact", "op": "is", "value": ID},
    {"field": "contact", "op": "in", "value": [ID, str(new_id())]},
    {"field": "contact", "op": "present"},
    {"field": "document_type", "op": "missing"},
    {"field": "tags", "op": "contains", "value": ID},
    {"field": "tags", "op": "in", "value": [ID]},
    {"field": "tags", "op": "present"},
    {"field": "channel", "op": "is", "value": "migration"},
    {"field": "channel", "op": "in", "value": ["web", "api"]},
    {"field": "text", "op": "contains", "value": "Invoice"},
    {"field": "text", "op": "matches", "value": r"\bR-\d+", "case_sensitive": True},
    {"field": "document_date", "op": "is", "value": "2026-03-31"},
    {"field": "document_date", "op": "lt", "value": "2026-03-31"},
    {"field": "document_date", "op": "present"},
    {"field": "field", "op": "gt", "field_id": ID, "value": "12"},
    {"field": "field", "op": "in", "field_id": ID, "value": ["a", "b"]},
    {"field": "field", "op": "matches", "field_id": ID, "value": "^DE"},
    {"field": "field", "op": "missing", "field_id": ID},
]


@pytest.mark.parametrize("data", VALID_CONDITIONS)
def test_valid_condition_round_trips(data: JsonObject) -> None:
    assert condition_to_json(condition_from_json(data)) == data


INVALID_CONDITIONS: list[tuple[JsonObject, str]] = [
    # operators that do not fit the field
    ({"field": "contact", "op": "contains", "value": ID}, "'contains' does not apply to contact"),
    ({"field": "document_type", "op": "matches", "value": "x"}, "does not apply to document_type"),
    ({"field": "tags", "op": "is", "value": ID}, "'is' does not apply to tags"),
    ({"field": "channel", "op": "present"}, "'present' does not apply to channel"),
    ({"field": "text", "op": "is", "value": "x"}, "'is' does not apply to text"),
    ({"field": "text", "op": "gt", "value": "x"}, "'gt' does not apply to text"),
    ({"field": "document_date", "op": "in", "value": ["2026-01-01"]}, "does not apply"),
    ({"field": "document_date", "op": "contains", "value": "2026"}, "does not apply"),
    # 'in' takes a non-empty list of at most MAX_LIST
    ({"field": "contact", "op": "in", "value": []}, "'in' takes a non-empty list"),
    ({"field": "contact", "op": "in", "value": ID}, "'in' takes a non-empty list"),
    (
        {"field": "tags", "op": "in", "value": [str(new_id()) for _ in range(MAX_LIST + 1)]},
        f"'in' takes at most {MAX_LIST} values",
    ),
    # present and missing take no value, the others exactly one
    ({"field": "contact", "op": "present", "value": ID}, "'present' takes no value"),
    ({"field": "tags", "op": "missing", "value": [ID]}, "'missing' takes no value"),
    ({"field": "contact", "op": "is"}, "'is' takes one value"),
    ({"field": "channel", "op": "is", "value": ["web"]}, "'is' takes one value"),
    # case_sensitive only for matches
    (
        {"field": "text", "op": "contains", "value": "x", "case_sensitive": True},
        "case_sensitive applies to 'matches' only",
    ),
    ({"field": "text", "op": "matches", "value": "x", "case_sensitive": "yes"}, "true or false"),
    # values
    ({"field": "channel", "op": "is", "value": "mail"}, "unknown channel 'mail'"),
    ({"field": "channel", "op": "in", "value": ["web", "fax"]}, "unknown channel 'fax'"),
    ({"field": "contact", "op": "is", "value": "not-an-id"}, "'not-an-id' is no id"),
    ({"field": "tags", "op": "contains", "value": 42}, "42 is no id"),
    ({"field": "document_type", "op": "in", "value": [ID, "x"]}, "'x' is no id"),
    ({"field": "document_date", "op": "is", "value": "2026-02-30"}, "is no date"),
    ({"field": "document_date", "op": "gt", "value": "31.03.2026"}, "is no date"),
    ({"field": "document_date", "op": "lt", "value": 20260331}, "is no date"),
    ({"field": "text", "op": "matches", "value": "("}, "invalid regular expression"),
    (
        {"field": "field", "op": "matches", "field_id": ID, "value": "[a-"},
        "invalid regular expression",
    ),
    ({"field": "text", "op": "contains", "value": "  "}, "expected non-empty text"),
    ({"field": "text", "op": "contains", "value": "x" * (MAX_TEXT + 1)}, f"{MAX_TEXT} characters"),
    (
        {"field": "text", "op": "matches", "value": "x" * (MAX_PATTERN + 1)},
        f"{MAX_PATTERN} characters",
    ),
    ({"field": "field", "op": "contains", "field_id": ID, "value": 7}, "non-empty text"),
    # field_id exactly for field conditions
    ({"field": "field", "op": "present"}, "field_id is given exactly"),
    ({"field": "contact", "op": "present", "field_id": ID}, "field_id is given exactly"),
    ({"field": "field", "op": "present", "field_id": "x"}, "field_id: 'x' is no id"),
    # unknown names
    ({"field": "amount", "op": "is", "value": "1"}, "field: unknown value 'amount'"),
    ({"field": "contact", "op": "between", "value": ID}, "op: unknown value 'between'"),
    ({"field": "contact", "op": "present", "negate": True}, "unknown key 'negate'"),
]


@pytest.mark.parametrize(("data", "message"), INVALID_CONDITIONS)
def test_invalid_condition_is_rejected(data: JsonObject, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        condition_from_json(data)


def test_in_takes_up_to_max_list_values() -> None:
    values: list[JsonValue] = [str(new_id()) for _ in range(MAX_LIST)]

    condition = Condition(field=ConditionField.CONTACT, op=Operator.IN, value=values)

    assert condition.values == values


@pytest.mark.parametrize(
    "value",
    [
        {"amount": "1", "currency": "EUR"},
        [["web"]],
    ],
)
def test_channel_value_of_wrong_shape_is_a_validation_error(value: JsonValue) -> None:
    # A JSON object (or a list inside `in`) as channel must be rejected like any other value,
    # not crash with TypeError (unhashable dict/list in a set membership test).
    op = "in" if isinstance(value, list) else "is"
    with pytest.raises(ValidationError, match="unknown channel"):
        condition_from_json({"field": "channel", "op": op, "value": value})


def test_group_rules() -> None:
    with pytest.raises(ValidationError, match="at least one condition"):
        Group(mode="all", items=())
    with pytest.raises(ValidationError, match="'all' or 'any'"):
        Group(mode="none", items=(ALWAYS,))  # type: ignore[arg-type]


def test_group_lists_conditions_depth_first() -> None:
    a = Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value="a")
    b = Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value="b")
    c = Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value="c")

    tree = group(a, group(b, group(c)), mode="any")

    assert tree.conditions() == [a, b, c]
    assert tree.depth == 3


def test_negated_group_from_json() -> None:
    parsed = group_from_json({"any": [{"field": "contact", "op": "missing"}], "not": True})

    assert parsed.negate is True
    assert parsed.mode == "any"


# --- limits -----------------------------------------------------------------------------------


def nested(depth: int) -> Group:
    tree = group(ALWAYS)
    for _ in range(depth - 1):
        tree = group(tree)
    return tree


def test_conditions_nest_at_most_max_depth() -> None:
    assert definition(conditions=nested(MAX_DEPTH)).conditions.depth == MAX_DEPTH
    with pytest.raises(ValidationError, match=f"nested at most {MAX_DEPTH} deep"):
        definition(conditions=nested(MAX_DEPTH + 1))


def test_a_rule_has_at_most_max_conditions() -> None:
    definition(conditions=group(*[ALWAYS] * MAX_CONDITIONS))
    with pytest.raises(ValidationError, match=f"at most {MAX_CONDITIONS} conditions"):
        # counted across nested groups
        definition(conditions=group(*[ALWAYS] * (MAX_CONDITIONS - 1), group(ALWAYS, ALWAYS)))


def test_a_rule_has_at_most_max_actions() -> None:
    def tags(count: int) -> list[Action]:
        return [AddTags(frozenset({TagId(new_id())})) for _ in range(count)]

    definition(*tags(MAX_ACTIONS))
    with pytest.raises(ValidationError, match=f"at most {MAX_ACTIONS} actions"):
        definition(*tags(MAX_ACTIONS + 1))


def test_rule_name() -> None:
    assert definition(name="  Invoices  ").name == "Invoices"
    assert definition(name="x" * MAX_NAME).name == "x" * MAX_NAME
    with pytest.raises(ValidationError, match=f"more than {MAX_NAME} characters"):
        definition(name="x" * (MAX_NAME + 1))
    with pytest.raises(ValidationError, match="must not be empty"):
        definition(name="   ")


@pytest.mark.parametrize("priority", [0, 1, 100, 1000])
def test_priority_in_range(priority: int) -> None:
    rule = RuleDefinition(
        name="R", conditions=group(ALWAYS), actions=(ForceReview("x"),), priority=priority
    )
    assert rule.priority == priority


@pytest.mark.parametrize("priority", [-1, 1001, True])
def test_priority_out_of_range(priority: int) -> None:
    with pytest.raises(ValidationError, match="from 0 to 1000"):
        RuleDefinition(
            name="R", conditions=group(ALWAYS), actions=(ForceReview("x"),), priority=priority
        )


def test_definition_needs_triggers_and_actions() -> None:
    with pytest.raises(ValidationError, match="at least one trigger"):
        RuleDefinition(
            name="R", conditions=group(ALWAYS), actions=(ForceReview("x"),), triggers=frozenset()
        )
    with pytest.raises(ValidationError, match="at least one action"):
        RuleDefinition(name="R", conditions=group(ALWAYS), actions=())


@pytest.mark.parametrize(
    "actions",
    [
        (SetContact(CONTACT), SetContact(OTHER_CONTACT)),
        (SetDrawer(DRAWER), SetDrawer(DrawerId(new_id()))),
        (SetTitle("a"), SetTitle("b")),
        (SetField(FIELD, "a"), SetField(FIELD, "b")),
    ],
)
def test_an_action_may_not_set_the_same_field_twice(actions: tuple[Action, ...]) -> None:
    with pytest.raises(ValidationError, match="sets the same field twice"):
        definition(*actions)


def test_repeatable_actions() -> None:
    rule = definition(
        AddTags(frozenset({TAG})),
        AddTags(frozenset({OTHER_TAG})),
        RemoveTags(frozenset({TAG})),
        ForceReview("a"),
        ForceReview("b"),
        SetField(FIELD, "a"),
        SetField(FieldId(new_id()), "b"),
    )
    assert len(rule.actions) == 7


# --- actions ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "Invoice",
        "{contact}",
        "{contact} {document_type} {document_date} {filename}",
        "{document_date} - {contact}",
    ],
)
def test_title_template_with_known_placeholders(template: str) -> None:
    assert SetTitle(template).template == template


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("{amount}", r"unknown placeholder \{amount\}"),
        ("{Contact}", r"unknown placeholder \{Contact\}"),
        ("Invoice {}", r"unknown placeholder \{\}"),
        ("{contact} {date}", r"unknown placeholder \{date\}"),
        ("", "non-empty text"),
        ("   ", "non-empty text"),
        ("x" * (MAX_TEXT + 1), f"longer than {MAX_TEXT}"),
    ],
)
def test_invalid_title_template(template: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SetTitle(template)


def test_unknown_placeholder_is_rejected_from_json() -> None:
    with pytest.raises(ValidationError, match=r"actions\[0\]: unknown placeholder"):
        definition_from_json(
            {**VALID_RULE, "actions": [{"type": "set_title", "template": "{sender}"}]}
        )


def test_action_values() -> None:
    with pytest.raises(ValidationError, match="at least one tag"):
        AddTags(frozenset())
    with pytest.raises(ValidationError, match="at least one tag"):
        RemoveTags(frozenset())
    with pytest.raises(ValidationError, match=f"at most {MAX_LIST} tags"):
        AddTags(frozenset(TagId(new_id()) for _ in range(MAX_LIST + 1)))
    with pytest.raises(ValidationError, match="needs a value"):
        SetField(FIELD, None)
    with pytest.raises(ValidationError, match="non-empty text"):
        ForceReview(" ")
    with pytest.raises(ValidationError, match="reason: expected text"):
        action_from_json({"type": "force_review", "reason": 1})


# --- scope ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [SetDrawer(DRAWER), SetContact(CONTACT), SetDocumentType(TYPE), SetTitle("{contact}")],
)
def test_global_rules_cannot_change_who_sees_or_files(action: Action) -> None:
    with pytest.raises(ValidationError, match=f"a global rule cannot {action.type}"):
        check_scope(definition(action), RuleScope.GLOBAL)
    check_scope(definition(action), RuleScope.USER)


def test_global_actions() -> None:
    allowed = definition(
        AddTags(frozenset({TAG})),
        RemoveTags(frozenset({OTHER_TAG})),
        SetField(FIELD, "x"),
        ForceReview("look"),
    )
    assert all(isinstance(action, GLOBAL_ACTIONS) for action in allowed.actions)
    check_scope(allowed, RuleScope.GLOBAL)


def test_global_rule_is_created_and_changed_with_global_actions_only() -> None:
    with pytest.raises(ValidationError, match="a global rule cannot set_drawer"):
        Rule.create(
            scope=RuleScope.GLOBAL,
            owner_id=None,
            definition=definition(SetDrawer(DRAWER)),
            by=ADMIN,
            now=NOW,
        )
    rule = Rule.create(
        scope=RuleScope.GLOBAL, owner_id=None, definition=definition(), by=ADMIN, now=NOW
    )
    with pytest.raises(ValidationError, match="a global rule cannot set_contact"):
        rule.change(definition(SetContact(CONTACT)), ADMIN, NOW)
    assert rule.current.number == 1


# --- fields -------------------------------------------------------------------------------


def field_rule(
    attr: FieldDefinition, op: Operator, value: JsonValue = None, *actions: Action
) -> RuleDefinition:
    return definition(
        *actions,
        conditions=group(
            Condition(field=ConditionField.FIELD, op=op, value=value, field_id=attr.id)
        ),
    )


@pytest.mark.parametrize(
    ("data_type", "op", "value"),
    [
        (FieldType.TEXT, Operator.IS, "ACME"),
        (FieldType.TEXT, Operator.CONTAINS, "acme"),
        (FieldType.TEXT, Operator.MATCHES, "^A"),
        (FieldType.LINK, Operator.IS, "https://example.org"),
        (FieldType.NUMBER, Operator.GT, "12.5"),
        (FieldType.NUMBER, Operator.IS, 3),
        (FieldType.AMOUNT, Operator.LT, {"amount": "100", "currency": "EUR"}),
        (FieldType.DATE, Operator.GT, "2026-01-01"),
        (FieldType.BOOLEAN, Operator.IS, True),
        (FieldType.CHOICE, Operator.IS, "monthly"),
        (FieldType.CHOICE, Operator.IN, ["monthly", "yearly"]),
        (FieldType.CHOICE, Operator.PRESENT, None),
    ],
)
def test_field_condition_fits_data_type(
    data_type: FieldType, op: Operator, value: JsonValue
) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    attr = field(data_type, choices)

    check_fields(field_rule(attr, op, value), {attr.id: attr})


@pytest.mark.parametrize(
    ("data_type", "op", "value"),
    [
        (FieldType.NUMBER, Operator.CONTAINS, "1"),
        (FieldType.NUMBER, Operator.IN, ["1"]),
        (FieldType.AMOUNT, Operator.MATCHES, "1"),
        (FieldType.DATE, Operator.IN, ["2026-01-01"]),
        (FieldType.BOOLEAN, Operator.GT, True),
        (FieldType.BOOLEAN, Operator.IN, [True]),
        (FieldType.CHOICE, Operator.CONTAINS, "month"),
        (FieldType.CHOICE, Operator.GT, "monthly"),
        (FieldType.TEXT, Operator.GT, "a"),
        (FieldType.LINK, Operator.LT, "https://example.org"),
    ],
)
def test_field_operator_must_fit_data_type(
    data_type: FieldType, op: Operator, value: JsonValue
) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    attr = field(data_type, choices)

    with pytest.raises(ValidationError, match=f"operator '{op}' does not apply to field"):
        check_fields(field_rule(attr, op, value), {attr.id: attr})


@pytest.mark.parametrize(
    ("data_type", "op", "value"),
    [
        (FieldType.NUMBER, Operator.IS, "abc"),
        (FieldType.NUMBER, Operator.GT, True),
        (FieldType.AMOUNT, Operator.GT, "100"),
        (FieldType.AMOUNT, Operator.GT, {"amount": "100", "currency": "euro"}),
        (FieldType.DATE, Operator.LT, "2026-02-30"),
        (FieldType.BOOLEAN, Operator.IS, "true"),
        (FieldType.CHOICE, Operator.IS, "weekly"),
        (FieldType.CHOICE, Operator.IN, ["monthly", "weekly"]),
        (FieldType.LINK, Operator.IS, "ftp://example.org"),
    ],
)
def test_field_condition_value_must_fit(
    data_type: FieldType, op: Operator, value: JsonValue
) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    attr = field(data_type, choices)

    with pytest.raises(ValidationError, match="does not accept"):
        check_fields(field_rule(attr, op, value), {attr.id: attr})


@pytest.mark.parametrize(
    ("data_type", "value", "fits"),
    [
        (FieldType.AMOUNT, {"amount": "12.50", "currency": "EUR"}, True),
        (FieldType.AMOUNT, "12.50", False),
        (FieldType.NUMBER, "7", True),
        (FieldType.NUMBER, "seven", False),
        (FieldType.DATE, "2026-03-31", True),
        (FieldType.DATE, "31.03.2026", False),
        (FieldType.CHOICE, "yearly", True),
        (FieldType.CHOICE, "weekly", False),
        (FieldType.BOOLEAN, False, True),
        (FieldType.TEXT, " ", False),
    ],
)
def test_set_field_value_must_fit(data_type: FieldType, value: JsonValue, fits: bool) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    attr = field(data_type, choices)
    rule = definition(SetField(attr.id, value))

    if fits:
        check_fields(rule, {attr.id: attr})
    else:
        with pytest.raises(ValidationError, match="does not accept"):
            check_fields(rule, {attr.id: attr})


def test_missing_field_is_rejected() -> None:
    attr = field(FieldType.TEXT)
    with pytest.raises(ValidationError, match=f"field {attr.id} does not exist"):
        check_fields(field_rule(attr, Operator.PRESENT), {})
    with pytest.raises(ValidationError, match="does not exist"):
        check_fields(definition(SetField(attr.id, "x")), {})


def test_errors_of_stored_data_and_scope_name_the_place() -> None:
    """So that a client puts the message next to the condition or action concerned."""
    number = field(FieldType.NUMBER)
    condition = Condition(
        field=ConditionField.FIELD, op=Operator.IS, value="abc", field_id=number.id
    )
    nested = definition(
        AddTags(frozenset({TAG})),
        SetField(number.id, "seven"),
        conditions=group(ALWAYS, group(ALWAYS, condition, mode="any")),
    )
    with pytest.raises(
        ValidationError, match=r"^conditions\.all\[1\]\.any\[1\]: .*does not accept"
    ):
        check_fields(nested, {number.id: number})
    fitting = definition(SetField(number.id, "seven"), AddTags(frozenset({TAG})))
    with pytest.raises(ValidationError, match=r"^actions\[0\]: .*does not accept"):
        check_fields(fitting, {number.id: number})
    with pytest.raises(ValidationError, match=r"^actions\[1\]: a global rule cannot set_drawer"):
        check_scope(
            definition(AddTags(frozenset({TAG})), SetDrawer(DrawerId(new_id()))),
            RuleScope.GLOBAL,
        )
    with pytest.raises(ValidationError, match=r"^actions\[2\]: an action sets the same field"):
        definition(SetTitle("A"), AddTags(frozenset({TAG})), SetTitle("B"))


# --- references -------------------------------------------------------------------------------


def test_references() -> None:
    in_condition = ContactId(new_id())
    type_in_condition = DocumentTypeId(new_id())
    tags = [TagId(new_id()) for _ in range(3)]
    attr = FieldId(new_id())
    rule = definition(
        SetDrawer(DRAWER),
        SetContact(CONTACT),
        SetDocumentType(TYPE),
        AddTags(frozenset({tags[2]})),
        RemoveTags(frozenset({OTHER_TAG})),
        SetField(FIELD, "x"),
        conditions=group(
            Condition(field=ConditionField.CONTACT, op=Operator.IN, value=[str(in_condition), ID]),
            Condition(
                field=ConditionField.DOCUMENT_TYPE, op=Operator.IS, value=str(type_in_condition)
            ),
            group(
                Condition(field=ConditionField.TAGS, op=Operator.CONTAINS, value=str(tags[0])),
                Condition(field=ConditionField.TAGS, op=Operator.IN, value=[str(tags[1])]),
                mode="any",
            ),
            Condition(field=ConditionField.FIELD, op=Operator.IS, value=ID, field_id=attr),
            # text values and other values that look like ids are no references
            Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value=str(OTHER_CONTACT)),
            Condition(field=ConditionField.CONTACT, op=Operator.PRESENT),
            ALWAYS,
        ),
    )

    assert rule.references() == References(
        contacts=frozenset({in_condition, ContactId(UUID(ID)), CONTACT}),
        document_types=frozenset({type_in_condition, TYPE}),
        tags=frozenset({*tags, OTHER_TAG}),
        fields=frozenset({attr, FIELD}),
        drawers=frozenset({DRAWER}),
    )


def test_uses_text_and_patterns() -> None:
    attr = FieldId(new_id())
    rule = definition(
        conditions=group(
            Condition(
                field=ConditionField.TEXT, op=Operator.MATCHES, value="R-\\d+", case_sensitive=True
            ),
            Condition(field=ConditionField.FIELD, op=Operator.MATCHES, value="^DE", field_id=attr),
            Condition(field=ConditionField.TEXT, op=Operator.CONTAINS, value="Invoice"),
        )
    )

    assert rule.uses_text
    assert rule.patterns() == {("text", "R-\\d+", True), (str(attr), "^DE", False)}
    assert not definition().uses_text
    assert definition().patterns() == set()


# --- rule -------------------------------------------------------------------------------------


def user_rule() -> Rule:
    return Rule.create(
        scope=RuleScope.USER, owner_id=OWNER, definition=definition(), by=OWNER, now=NOW
    )


def test_create_rule() -> None:
    rule = user_rule()

    assert rule.current.number == 1
    assert rule.current.rule_id == rule.id
    assert rule.current.created_by == OWNER
    assert rule.definition == definition()
    assert rule.is_active


def test_change_makes_a_new_version() -> None:
    rule = user_rule()
    first = rule.current
    later = NOW + timedelta(hours=1)
    changed = definition(SetContact(CONTACT), name="Invoices from ACME")

    version = rule.change(changed, ADMIN, later)

    assert version is rule.current
    assert (version.number, version.definition, version.created_by) == (2, changed, ADMIN)
    assert version.created_at == later and rule.updated_at == later
    assert first.number == 1 and first.definition == definition()  # old version stays readable
    assert rule.change(definition(), OWNER, later).number == 3


def test_enable_and_disable_make_no_version() -> None:
    rule = user_rule()
    later = NOW + timedelta(minutes=5)

    rule.disable(later, "contact deleted")
    assert (rule.enabled, rule.disabled_reason, rule.is_active) == (False, "contact deleted", False)
    assert rule.updated_at == later

    rule.enable(later)
    assert (rule.enabled, rule.disabled_reason, rule.is_active) == (True, None, True)
    rule.disable(later)
    assert rule.disabled_reason is None
    assert rule.current.number == 1


def test_delete_rule() -> None:
    rule = user_rule()
    later = NOW + timedelta(days=1)

    rule.delete(later)

    assert rule.deleted_at == later and rule.updated_at == later
    assert not rule.enabled and not rule.is_active
    with pytest.raises(ValidationError, match="is deleted"):
        rule.change(definition(), OWNER, later)
    with pytest.raises(ValidationError, match="is deleted"):
        rule.enable(later)
    with pytest.raises(ValidationError, match="is deleted"):
        rule.disable(later)
    with pytest.raises(ValidationError, match="is deleted"):
        rule.delete(later)


def test_rule_invariants() -> None:
    with pytest.raises(ValidationError, match="global rules have no owner"):
        Rule.create(
            scope=RuleScope.GLOBAL, owner_id=ADMIN, definition=definition(), by=ADMIN, now=NOW
        )
    with pytest.raises(ValidationError, match="global rules have no owner"):
        Rule.create(scope=RuleScope.USER, owner_id=None, definition=definition(), by=ADMIN, now=NOW)
    rule = user_rule()
    with pytest.raises(ValidationError, match="belongs to another rule"):
        Rule(
            id=RuleId(new_id()),
            scope=RuleScope.USER,
            owner_id=OWNER,
            current=rule.current,
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValidationError, match="start at 1"):
        RuleVersion(
            rule_id=rule.id, number=0, definition=definition(), created_at=NOW, created_by=None
        )
    with pytest.raises(ValidationError, match="UTC"):
        RuleVersion(
            rule_id=rule.id,
            number=1,
            definition=definition(),
            created_at=NOW.replace(tzinfo=None),
            created_by=None,
        )


def test_rule_order() -> None:
    def make(priority: int, scope: RuleScope, minutes: int) -> Rule:
        return Rule.create(
            scope=scope,
            owner_id=None if scope is RuleScope.GLOBAL else OWNER,
            definition=RuleDefinition(
                name="R", conditions=group(ALWAYS), actions=(ForceReview("x"),), priority=priority
            ),
            by=ADMIN,
            now=NOW + timedelta(minutes=minutes),
        )

    low = make(50, RuleScope.USER, 0)
    user_newer = make(100, RuleScope.USER, 2)
    user_older = make(100, RuleScope.USER, 1)
    global_newest = make(100, RuleScope.GLOBAL, 3)
    high = make(900, RuleScope.USER, 4)

    ordered = sorted([low, user_newer, user_older, global_newest, high], key=lambda r: r.order)

    assert ordered == [high, global_newest, user_older, user_newer, low]


# --- applying to existing documents -----------------------------------------------------------


def application(
    *documents: DocumentId, accept: frozenset[DocumentId] = frozenset()
) -> RuleApplication:
    return RuleApplication.create(
        rule_id=RuleId(new_id()),
        rule_version=2,
        user_id=OWNER,
        documents=documents,
        accept_conflicts=accept,
        now=NOW,
    )


def test_application_progress() -> None:
    a, b, c, d = (DocumentId(new_id()) for _ in range(4))
    run = application(a, b, c, d, accept=frozenset({b}))
    assert (run.status, run.remaining) == (ApplicationStatus.QUEUED, (a, b, c, d))

    run.record(a, "applied")
    run.record(b, "unchanged")
    run.record(c, "the drawer is read-only")

    assert (run.position, run.applied, run.unchanged) == (3, 1, 1)
    assert run.skipped == [(c, "the drawer is read-only")]
    assert (run.status, run.remaining) == (ApplicationStatus.RUNNING, (d,))

    run.record(d, "applied")
    later = NOW + timedelta(minutes=1)
    run.finish(later)

    assert not run.remaining
    assert (run.status, run.error, run.finished_at) == (ApplicationStatus.DONE, None, later)


def test_application_fails_with_error() -> None:
    run = application(DocumentId(new_id()))

    run.finish(NOW, error="database gone")

    assert run.status is ApplicationStatus.FAILED
    assert run.error == "database gone"


def test_application_selection() -> None:
    a, b = DocumentId(new_id()), DocumentId(new_id())
    with pytest.raises(ValidationError, match="at least one document"):
        application()
    with pytest.raises(ValidationError, match="selected twice"):
        application(a, a)
    with pytest.raises(ValidationError, match="among the selected"):
        application(a, accept=frozenset({b}))

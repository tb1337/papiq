"""Response bodies that more than one inbound adapter (REST, MCP) returns."""

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from papiq.core.domain.fields import FieldValue, field_to_json
from papiq.core.domain.rule_engine import RuleReport
from papiq.core.domain.rules import RuleScope


class MoneyValue(BaseModel):
    amount: str = Field(examples=["12.50"], description="Decimal number as text, exact.")
    currency: str = Field(examples=["EUR"], description="ISO 4217 code.")


class PeriodValue(BaseModel):
    start: date = Field(examples=["2026-09-01"], description="First day, included.")
    end: date = Field(
        examples=["2026-09-30"], description="Last day, included; a single day has start = end."
    )


FieldJson = Annotated[
    str | bool | MoneyValue | PeriodValue,
    Field(
        description=(
            "By data type: text, choice and link: string; number: decimal as string; date: "
            "`YYYY-MM-DD`; boolean: true/false; amount: `{amount, currency}`; period: "
            "`{start, end}` as `YYYY-MM-DD`, both days included."
        )
    ),
]


_FIELD_JSON: TypeAdapter[str | bool | MoneyValue | PeriodValue] = TypeAdapter(FieldJson)


def field_json(value: FieldValue) -> str | bool | MoneyValue | PeriodValue:
    """A value as the API writes it: its JSON form (`field_to_json`) as the response schema
    has it."""
    return _FIELD_JSON.validate_python(field_to_json(value))


class RuleEffectOut(BaseModel):
    field: str = Field(
        description="`space`, `contact`, `document_type`, `topic`, `title`, `tags`, ..."
    )
    old: Any
    new: Any


class RuleNoteOut(BaseModel):
    field: str
    kind: Literal["skipped", "overruled", "refused", "conflict", "review", "yielded"] = Field(
        description=(
            "`overruled`: a person decided the field; `refused`: not allowed (rights, scope, "
            "unconfirmed values); `conflict`: rules or values disagree; `skipped`: something "
            "it refers to is gone or does not apply, or a template writes a missing value; "
            "`review`: the rule holds the document for a review; `yielded`: a document type's "
            "title pattern gave way to another rule that sets the title."
        )
    )
    reason: str


class RuleReportOut(BaseModel):
    """What one rule did."""

    rule_id: UUID
    version: int
    name: str
    scope: RuleScope
    applied: list[RuleEffectOut]
    notes: list[RuleNoteOut] = Field(description="Actions that were not applied, and why.")

    @classmethod
    def of(cls, report: RuleReport) -> "RuleReportOut":
        return cls.model_validate(report.to_json())

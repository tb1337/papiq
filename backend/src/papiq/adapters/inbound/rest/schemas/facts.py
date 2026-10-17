"""What a document shows of its contact: names, identifiers and codes."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.schemas.inbox import BoxOut
from papiq.adapters.inbound.schemas import MoneyValue
from papiq.core.domain.facts import Span
from papiq.core.domain.identifiers import IdentifierKind
from papiq.core.services.documents import CodeFacts, DocumentFacts


class SpanOut(BaseModel):
    start: int = Field(description="First character, from 0.")
    end: int = Field(description="After the last character.")

    @classmethod
    def of(cls, span: Span) -> "SpanOut":
        return cls(start=span.start, end=span.end)


class NamedContactOut(BaseModel):
    contact_id: UUID
    name: str = Field(description="The contact's name or alias that stands there.")
    alias: bool
    in_text: bool
    in_code: bool = Field(description="The payee of a GiroCode on the pages.")
    spans: list[SpanOut] = Field(description="Where it stands in the text.")


class FoundIdentifierOut(BaseModel):
    kind: IdentifierKind
    value: str = Field(description="Normalised, as a contact keeps it.")
    contact_id: UUID | None = Field(
        description="The contact it is an identifier of (entered or learned); null if none."
    )
    topic_id: UUID | None = Field(
        default=None, description="The topic of the contact it points to, if any."
    )
    in_text: bool
    in_code: bool = Field(description="A code on the pages holds it.")
    spans: list[SpanOut] = Field(description="Where it stands in the text.")
    other_contacts: int | None = Field(
        description=(
            "For admins: on the confirmed documents of how many contacts other than the "
            "document's it stands (the IBAN of one's own account stands on many); null for "
            "others."
        )
    )


class IdentifierFoundOut(BaseModel):
    kind: IdentifierKind
    value: str


class GiroCodeOut(BaseModel):
    """The credit transfer of a GiroCode (EPC QR code)."""

    name: str = Field(description="The payee.")
    iban: str
    bic: str | None
    amount: MoneyValue | None
    purpose: str | None


class CodePlaceOut(BaseModel):
    """Where a code stands on a page of the archive."""

    page: int = Field(description="From 1.")
    boxes: list[BoxOut] = Field(description="One per copy of the code on the page.")


class CodeOut(BaseModel):
    """A code once for its format and text, however often the pages show it."""

    format: str = Field(examples=["QR Code", "Code 128", "Data Matrix"])
    text: str = Field(description="What it holds, as it holds it; someone else's text.")
    places: list[CodePlaceOut] = Field(description="The pages it stands on, in order.")
    giro: GiroCodeOut | None = Field(description="A GiroCode, read as its credit transfer.")
    identifiers: list[IdentifierFoundOut]
    fields: list[UUID] = Field(
        description="Amount fields of the document with the GiroCode's amount as their value."
    )

    @classmethod
    def of(cls, item: CodeFacts) -> "CodeOut":
        giro = None
        if item.giro is not None:
            amount = item.giro.amount
            giro = GiroCodeOut(
                name=item.giro.name,
                iban=item.giro.iban,
                bic=item.giro.bic,
                amount=None
                if amount is None
                else MoneyValue(amount=str(amount.amount), currency=amount.currency),
                purpose=item.giro.purpose,
            )
        return cls(
            format=item.format,
            text=item.text,
            places=[
                CodePlaceOut(page=place.page, boxes=[BoxOut.of(box) for box in place.boxes])
                for place in item.places
            ],
            giro=giro,
            identifiers=[
                IdentifierFoundOut(kind=kind, value=value) for kind, value in item.identifiers
            ],
            fields=list(item.fields),
        )


class FactsOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "named": [],
                    "identifiers": [
                        {
                            "kind": "customer_number",
                            "value": "20047718",
                            "contact_id": None,
                            "in_text": True,
                            "in_code": False,
                            "spans": [{"start": 412, "end": 421}],
                            "other_contacts": 0,
                        }
                    ],
                    "codes": [],
                }
            ]
        }
    )

    contact_id: UUID | None = Field(description="The document's contact.")
    named: list[NamedContactOut]
    identifiers: list[FoundIdentifierOut] = Field(
        description="Those of contacts first, then those of none."
    )
    codes: list[CodeOut] | None = Field(
        description="The codes on the pages; null while they are not read (`read-codes`)."
    )

    @classmethod
    def of(cls, report: DocumentFacts) -> "FactsOut":
        others = report.other_contacts
        return cls(
            contact_id=report.contact_id,
            named=[
                NamedContactOut(
                    contact_id=item.contact_id,
                    name=item.name,
                    alias=item.alias,
                    in_text=item.in_text,
                    in_code=item.in_code,
                    spans=[SpanOut.of(span) for span in item.spans],
                )
                for item in report.facts.named
            ],
            identifiers=[
                FoundIdentifierOut(
                    kind=item.kind,
                    value=item.value,
                    contact_id=item.contact_id,
                    topic_id=item.topic_id,
                    in_text=item.in_text,
                    in_code=item.in_code,
                    spans=[SpanOut.of(span) for span in item.spans],
                    other_contacts=None if others is None else others.get(item.key, 0),
                )
                for item in report.facts.identifiers
            ],
            codes=None if report.codes is None else [CodeOut.of(item) for item in report.codes],
        )

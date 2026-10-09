"""Metadata that comes with a document taken over from another system (channel `migration`)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from papiq.core.domain.classification import field_from_json, field_to_json
from papiq.core.domain.documents import UNSET, DocumentChanges
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.fields import FieldDefinition, FieldValue
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue

IMPORTED = "imported"
"""Key of the receive log entry's output that holds the metadata, and the `model_version` of
the steps that applied it."""


@dataclass(frozen=True, kw_only=True)
class ImportedMetadata:
    """What the source system knew about a document. The classification steps apply it instead
    of asking the language model. `None` leaves a field unset."""

    title: str | None = None
    contact_id: ContactId | None = None
    document_type_id: DocumentTypeId | None = None
    tag_ids: frozenset[TagId] = frozenset()
    document_date: date | None = None
    fields: Mapping[FieldId, FieldValue] = field(default_factory=dict)

    def classification(self) -> DocumentChanges:
        """Title, contact, type and date as a change; the tags are added separately."""
        return DocumentChanges(
            title=UNSET if self.title is None else self.title,
            contact_id=UNSET if self.contact_id is None else self.contact_id,
            document_type_id=UNSET if self.document_type_id is None else self.document_type_id,
            document_date=UNSET if self.document_date is None else self.document_date,
        )

    def to_json(self) -> JsonObject:
        return {
            "title": self.title,
            "contact_id": None if self.contact_id is None else str(self.contact_id),
            "document_type_id": (
                None if self.document_type_id is None else str(self.document_type_id)
            ),
            "tag_ids": [str(tag) for tag in sorted(self.tag_ids)],
            "document_date": None if self.document_date is None else self.document_date.isoformat(),
            "fields": {str(key): field_to_json(value) for key, value in self.fields.items()},
        }

    @classmethod
    def from_json(
        cls, data: JsonValue, definitions: Mapping[FieldId, FieldDefinition]
    ) -> "ImportedMetadata":
        """Read what `to_json` wrote; the field values are checked against `definitions`."""
        if not isinstance(data, dict):
            raise ValidationError("imported metadata: expected an object")
        try:
            raw_fields = data.get("fields")
            fields: dict[FieldId, FieldValue] = {}
            for key, value in (raw_fields if isinstance(raw_fields, dict) else {}).items():
                field_id = FieldId(UUID(key))
                if field_id not in definitions:
                    raise ValidationError(f"field {key} no longer exists")
                fields[field_id] = field_from_json(definitions[field_id], value)
            return cls(
                title=_text(data.get("title")),
                contact_id=_id(data.get("contact_id"), ContactId),
                document_type_id=_id(data.get("document_type_id"), DocumentTypeId),
                tag_ids=frozenset(TagId(UUID(str(tag))) for tag in _list(data.get("tag_ids"))),
                document_date=_date(data.get("document_date")),
                fields=fields,
            )
        except (ValueError, TypeError) as error:
            raise ValidationError(f"imported metadata: {error}") from None


def _text(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _id[T](value: JsonValue, kind: type[T]) -> T | None:
    return None if value is None else kind(UUID(str(value)))  # type: ignore[call-arg]


def _list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _date(value: JsonValue) -> date | None:
    return None if value is None else date.fromisoformat(str(value))

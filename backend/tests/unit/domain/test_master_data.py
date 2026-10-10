from datetime import UTC, datetime

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.master_data import MAX_ALIASES, MAX_DESCRIPTION, Contact, DocumentType, Tag

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("kind", [Contact, DocumentType, Tag])
def test_master_data_has_a_name(kind: type[Contact | DocumentType | Tag]) -> None:
    item = kind.create(name=" Stadtwerke ", now=NOW)
    assert item.name == "Stadtwerke"
    item.rename("Stadtwerke Köln")
    assert item.name == "Stadtwerke Köln"
    with pytest.raises(ValidationError):
        item.rename(" ")
    with pytest.raises(ValidationError):
        kind.create(name="", now=NOW)


def test_contact_aliases() -> None:
    contact = Contact.create(
        name="INTER", now=NOW, aliases=[" INTER AG ", "inter ag", "INTER\n\x00KV"]
    )
    assert contact.aliases == ["INTER AG", "INTER KV"]
    assert contact.is_named("inter kv") and not contact.is_named("INTER Leben")
    with pytest.raises(ValidationError):
        contact.set_aliases(["Inter"])
    with pytest.raises(ValidationError):
        contact.set_aliases([" "])
    with pytest.raises(ValidationError):
        contact.set_aliases(f"Alias {number}" for number in range(MAX_ALIASES + 1))
    contact.rename("Inter AG")  # an alias that becomes the name is no longer an alias
    assert contact.aliases == ["INTER KV"]


def test_document_type_description() -> None:
    kind = DocumentType.create(name="Pay slip", now=NOW, description="  ")
    assert kind.description is None
    kind.describe(" Entgeltbescheinigung ")
    assert kind.description == "Entgeltbescheinigung"
    with pytest.raises(ValidationError):
        kind.describe("x" * (MAX_DESCRIPTION + 1))

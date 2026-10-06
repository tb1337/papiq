from datetime import UTC, datetime

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.master_data import Contact, DocumentType, Tag

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

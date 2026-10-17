"""The values a template sees: plain data, nested in mappings and lists. A value a document
does not have is left out, not given as None: the template tests it with `{% if %}` or
`| default`, and writing it is a missing value (`MissingValueError`)."""

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import PurePath

from papiq.core.domain.documents import Document
from papiq.core.domain.fields import FieldDefinition, FieldValue, Money, Url
from papiq.core.domain.periods import Period

type TemplateValue = (
    str
    | int
    | bool
    | Decimal
    | date
    | Money
    | Period
    | Sequence[TemplateValue]
    | Mapping[str, TemplateValue]
)

type Render = Callable[[str, Mapping[str, TemplateValue]], str]
"""A template renderer's `render` (the port `TemplateRenderer`), as the rule planner takes it."""


# What a template can use; `field.<key>` names a field (`FieldDefinition.key`).
NAMES = (
    "title",
    "filename",
    "original_filename",
    "contact",
    "document_type",
    "document_date",
    "added",
    "tags",
    "topic",
    "contact_groups",
    "document_type_groups",
    "space",
    "owner",
    "channel",
    "pages",
    "field",
)
FIELD = "field"


def template_values(
    document: Document,
    *,
    contact: str | None,
    document_type: str | None,
    space: str | None,
    owner: str | None,
    tags: Iterable[str],
    document_date: date | None,
    fields: Iterable[tuple[FieldDefinition, FieldValue | None]],
    contact_groups: Iterable[str] = (),
    document_type_groups: Iterable[str] = (),
    topic: str | None = None,
) -> dict[str, TemplateValue]:
    """The values of `document` as a template sees them, the names already resolved (as rules
    leave them); what the document does not have is left out. `tags`: those set (not their
    ancestors); `topic`: its name (not its path); `contact_groups`, `document_type_groups`: the
    groups the contact and the type are in (not their ancestors). Lists are sorted by name."""

    def ordered(items: Iterable[str]) -> list[TemplateValue]:
        return [*sorted(items, key=str.casefold)]

    names = ordered(tags)
    given: dict[str, TemplateValue | None] = {
        "title": document.title,
        "filename": PurePath(document.original_filename).stem,
        "original_filename": document.original_filename,
        "contact": contact,
        "document_type": document_type,
        "document_date": document_date,
        "added": document.created_at.date(),
        "tags": names,
        "topic": topic,
        "contact_groups": ordered(contact_groups),
        "document_type_groups": ordered(document_type_groups),
        "space": space,
        "owner": owner,
        "channel": document.channel.value,
        "pages": document.page_count,
        FIELD: {definition.key: _field(value) for definition, value in fields if value is not None},
    }
    return {name: value for name, value in given.items() if value is not None}


def _field(value: FieldValue) -> TemplateValue:
    return value.value if isinstance(value, Url) else value


def writing_templates(values: Mapping[str, TemplateValue]) -> dict[str, str]:
    """For each of the `values` (`template_values`), the template that writes it:
    `{{ contact }}`, `{{ field.la_brutto }}`, a list joined (`{{ tags | join: ', ' }}`)."""
    paths: list[tuple[str, TemplateValue]] = []
    for name, value in values.items():
        if name == FIELD and isinstance(value, Mapping):
            paths.extend((f"{FIELD}.{key}", item) for key, item in value.items())
        else:
            paths.append((name, value))
    return {
        path: (
            f"{{{{ {path} | join: ', ' }}}}"
            if isinstance(value, Sequence) and not isinstance(value, str)
            else f"{{{{ {path} }}}}"
        )
        for path, value in paths
    }


def unknown_values(paths: Iterable[str], field_keys: Collection[str]) -> list[str]:
    """The paths of a template (`TemplateRenderer.check`) that name nothing a template can
    use: an unknown name, or a field that is not among `field_keys`."""
    unknown: list[str] = []
    for path in sorted(paths):
        name, _, rest = path.partition(".")
        known_field = name != FIELD or rest.partition(".")[0] in field_keys
        if name not in NAMES or not known_field:
            unknown.append(path)
    return unknown


def rename_field_key(template: str, old: str, new: str) -> str:
    """`template` with `field.<old>` written `field.<new>` (also `field["<old>"]`); a key that
    only begins the same (`field.<old>_x`) stays."""
    pattern = re.compile(
        rf"(?<![\w.]){FIELD}(?:\.{re.escape(old)}(?!\w)|\[(['\"]){re.escape(old)}\1\])"
    )
    return pattern.sub(f"{FIELD}.{new}", template)


def uses_field(paths: Iterable[str], key: str) -> bool:
    """Whether the paths a template uses (`TemplateRenderer.check`) name the field `key`."""
    return any(path == f"{FIELD}.{key}" or path.startswith(f"{FIELD}.{key}.") for path in paths)

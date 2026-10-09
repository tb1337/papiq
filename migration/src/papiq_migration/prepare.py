"""What a Paperless document becomes in Papiq: owner, drawer and the metadata that goes with
the upload, in terms of Papiq's ids. Used by the migration (to upload) and by the check (to
compare)."""

import json
from dataclasses import dataclass, field
from typing import Any

from papiq_migration.mapping import (
    ASN_SPEC,
    NOTES_LIMIT,
    NOTES_SPEC,
    Access,
    FieldSpec,
    access_of,
    convert_value,
    document_date,
    document_title,
    field_key,
    norm,
    notes_text,
)

SUPPORTED = ("application/pdf", "image/jpeg", "image/png", "image/tiff")
METADATA_LIMIT = 45_000  # bytes of the metadata field (Papiq allows 48 KiB)


@dataclass
class Ids:
    """Paperless ids to the Papiq ids they became."""

    admin: str  # the executing admin: owns documents without an owner
    inactive: set[str] = field(default_factory=set)  # Papiq users that cannot own or share
    users: dict[int, str] = field(default_factory=dict)  # Paperless user id -> Papiq user id
    usernames: dict[str, str] = field(default_factory=dict)  # norm(username) -> Papiq user id
    contacts: dict[int, str] = field(default_factory=dict)
    document_types: dict[int, str] = field(default_factory=dict)
    tags: dict[int, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)  # field key -> Papiq id
    specs: dict[str, FieldSpec] = field(default_factory=dict)  # usable fields by key


@dataclass
class Prepared:
    id: int
    title: str
    filename: str
    media_type: str
    skip: str | None
    owner: str  # Papiq user id
    owner_name: str | None  # Paperless name; None: the executing admin
    shares: tuple[tuple[str, str], ...]
    metadata: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    access: Access | None = None

    def drawer_key(self) -> str | None:
        """The key of the drawer this document needs; None: the owner's default drawer."""
        if not self.shares:
            return None
        return self.owner + "|" + ";".join(f"{user}:{level}" for user, level in self.shares)


def prepare(
    document: dict[str, Any],
    *,
    ids: Ids,
    users: dict[int, dict[str, Any]],
    custom_fields: dict[int, dict[str, Any]],
    currency: str,
) -> Prepared:
    """Owner, shares and metadata of one document, and why it cannot be taken over, if so."""
    notes: list[str] = []
    title, title_notes = document_title(document)
    notes += title_notes
    access = access_of(document, users)
    notes += access.notes
    owner_id = ids.usernames.get(norm(access.owner)) if access.owner else None
    mime = str(document.get("mime_type") or "")
    skip = None
    if document.get("deleted_at"):
        skip = "the document is in Paperless' trash"
    elif document.get("root_document") not in (None, document["id"]):
        skip = f"the file is a version of document {document['root_document']}"
    elif mime not in SUPPORTED:
        skip = f"Papiq takes PDF, JPEG, PNG and TIFF; this file is {mime or 'of unknown type'}"
    final_owner = owner_id or ids.admin
    shares: list[tuple[str, str]] = []
    for name, level in access.shares:
        recipient = ids.usernames.get(norm(name))
        if recipient is None or recipient in ids.inactive:
            notes.append(f"shared with {name}, who is not an active user in Papiq: not shared")
        elif recipient == final_owner:
            notes.append(f"shared with {name}, who owns the document in Papiq: not shared")
        else:
            shares.append((name, level))

    further = [v for v in document.get("versions") or [] if not v.get("is_root")]
    if further:
        notes.append(f"{len(further)} further versions of the file are not taken over")
    fields: dict[str, Any] = {}
    asn = document.get("archive_serial_number")
    if asn is not None and ASN_SPEC.key in ids.fields:
        fields[ids.fields[ASN_SPEC.key]] = str(asn)
    text, text_notes = notes_text(document.get("notes") or [])
    notes += text_notes
    if text and NOTES_SPEC.key in ids.fields:
        fields[ids.fields[NOTES_SPEC.key]] = text
    for item in document.get("custom_fields") or []:
        custom_field = custom_fields.get(int(item["field"]))
        if custom_field is None:
            notes.append(f"custom field {item['field']} does not exist: value not taken over")
            continue
        key = field_key(custom_field["id"])
        if key not in ids.specs:
            if custom_field["data_type"] == "documentlink":
                notes.append(
                    f"'{custom_field['name']}': link to documents {item.get('value')} "
                    "is not taken over"
                )
            elif item.get("value") not in (None, "", []):
                notes.append(f"'{custom_field['name']}': no field in Papiq, value not taken over")
            continue
        converted = convert_value(custom_field, item.get("value"), currency)
        if converted.note and converted.note != "empty value":
            notes.append(f"'{custom_field['name']}': {converted.note}")
        if converted.value is not None:
            fields[ids.fields[key]] = converted.value

    metadata: dict[str, Any] = {
        "title": title,
        "contact_id": ids.contacts.get(document["correspondent"])
        if document.get("correspondent") is not None
        else None,
        "document_type_id": ids.document_types.get(document["document_type"])
        if document.get("document_type") is not None
        else None,
        "tag_ids": sorted({ids.tags[tag] for tag in document.get("tags") or [] if tag in ids.tags}),
        "document_date": document_date(document),
        "fields": fields,
    }
    while len(json.dumps(metadata)) > METADATA_LIMIT and fields:
        # Too large for the upload: leave out the biggest value, the notes first.
        notes_id = ids.fields.get(NOTES_SPEC.key)
        biggest = (
            notes_id
            if notes_id in fields
            else max(fields, key=lambda key: len(json.dumps(fields[key])))
        )
        del fields[biggest]
        notes.append("the metadata was too large: one value is not taken over")
    return Prepared(
        id=int(document["id"]),
        title=title,
        filename=str(document.get("original_file_name") or f"document-{document['id']}"),
        media_type=mime,
        skip=skip,
        owner=owner_id or ids.admin,
        owner_name=access.owner if owner_id else None,
        shares=tuple(shares),
        metadata=metadata,
        notes=notes,
        access=access,
    )


__all__ = ["NOTES_LIMIT", "SUPPORTED", "Ids", "Prepared", "prepare"]

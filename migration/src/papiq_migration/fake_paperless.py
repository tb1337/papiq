"""A stand-in for the Paperless-ngx REST API, for tests: serves a prepared archive through
`httpx2.MockTransport`. Only the endpoints the migration reads exist, and only GET."""

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx2

TOKEN = "paperless-test-key"


@dataclass
class Archive:
    """What the fake serves; every list is the `results` of the endpoint of the same name."""

    users: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    correspondents: list[dict[str, Any]] = field(default_factory=list)
    document_types: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    custom_fields: list[dict[str, Any]] = field(default_factory=list)
    storage_paths: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    saved_views: int = 0
    workflows: int = 0
    mail_rules: int = 0
    share_links: int = 0
    files: dict[int, bytes] = field(default_factory=dict)
    page_size_limit: int = 100


class FakePaperless:
    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self.requests: list[tuple[str, str]] = []
        self.downloads: list[int] = []

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.path))
        headers = {"x-version": "3.3.0", "x-api-version": "10"}
        if request.headers.get("authorization") != f"Token {TOKEN}":
            return httpx2.Response(401, json={"detail": "Invalid token."}, headers=headers)
        if request.method != "GET":
            return httpx2.Response(405, json={"detail": "read only"}, headers=headers)
        path = request.url.path.strip("/").split("/")
        query = parse_qs(request.url.query.decode())
        if path[:2] != ["api", path[1]] or len(path) < 2:
            return httpx2.Response(404, headers=headers)
        name = path[1]
        if name == "documents" and len(path) == 4 and path[3] == "download":
            document_id = int(path[2])
            if document_id not in self.archive.files:
                return httpx2.Response(404, headers=headers)
            self.downloads.append(document_id)
            return httpx2.Response(200, content=self.archive.files[document_id], headers=headers)
        counted = {
            "saved_views": self.archive.saved_views,
            "workflows": self.archive.workflows,
            "mail_rules": self.archive.mail_rules,
            "share_links": self.archive.share_links,
        }
        if name in counted:
            return httpx2.Response(
                200, json={"count": counted[name], "next": None, "results": []}, headers=headers
            )
        items: list[dict[str, Any]] | None = getattr(self.archive, name, None)
        if items is None:
            return httpx2.Response(404, headers=headers)
        page = int(query.get("page", ["1"])[0])
        size = min(int(query.get("page_size", ["25"])[0]), self.archive.page_size_limit)
        window = items[(page - 1) * size : page * size]
        more = page * size < len(items)
        body = {
            "count": len(items),
            "next": f"http://elsewhere/?page={page + 1}" if more else None,
            "results": window,
        }
        return httpx2.Response(200, content=json.dumps(body), headers=headers)


def pdf(number: int, base: bytes) -> bytes:
    """A distinct file for every number: the sample with a comment before its end marker."""
    body = base.rstrip()
    marker = b"%%EOF"
    head = body[: -len(marker)] if body.endswith(marker) else body
    return head + f"%document {number}\n".encode() + marker + b"\n"


def sample_archive(base_pdf: bytes, jpeg: bytes | None = None) -> Archive:
    """A small archive with the cases the migration has to handle: owners, a deleted owner and
    none, an inactive user, groups, permissions, every custom field type, notes, an ASN, a tag
    with a parent, a document Papiq cannot take (an e-mail) and one in the trash."""
    users = [
        {"id": 1, "username": "tobias", "is_active": True, "is_superuser": True, "groups": [1]},
        {"id": 2, "username": "anna", "is_active": True, "is_superuser": False, "groups": [1]},
        {"id": 3, "username": "bob", "is_active": True, "is_superuser": False, "groups": []},
        {"id": 4, "username": "gone", "is_active": False, "is_superuser": False, "groups": []},
    ]
    fields = [
        {"id": 1, "name": "Text", "data_type": "string", "extra_data": {}},
        {"id": 2, "name": "Long", "data_type": "longtext", "extra_data": {}},
        {"id": 3, "name": "Site", "data_type": "url", "extra_data": {}},
        {"id": 4, "name": "Due", "data_type": "date", "extra_data": {}},
        {"id": 5, "name": "Paid", "data_type": "boolean", "extra_data": {}},
        {"id": 6, "name": "Count", "data_type": "integer", "extra_data": {}},
        {"id": 7, "name": "Ratio", "data_type": "float", "extra_data": {}},
        {
            "id": 8,
            "name": "Net",
            "data_type": "monetary",
            "extra_data": {"default_currency": "USD"},
        },
        {"id": 9, "name": "Gross", "data_type": "monetary", "extra_data": {}},
        {
            "id": 10,
            "name": "Kind",
            "data_type": "select",
            "extra_data": {
                "select_options": [{"id": "a1", "label": "One"}, {"id": "b2", "label": "Two"}]
            },
        },
        {"id": 11, "name": "Related", "data_type": "documentlink", "extra_data": {}},
    ]
    empty: dict[str, Any] = {
        "view": {"users": [], "groups": []},
        "change": {"users": [], "groups": []},
    }

    def document(id: int, owner: int | None, **changes: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": id,
            "title": f"Document {id}",
            "created": "2024-03-31",
            "owner": owner,
            "correspondent": None,
            "document_type": None,
            "storage_path": None,
            "tags": [],
            "custom_fields": [],
            "archive_serial_number": None,
            "notes": [],
            "permissions": empty,
            "original_file_name": f"scan-{id}.pdf",
            "mime_type": "application/pdf",
            "versions": [],
            "root_document": None,
            "deleted_at": None,
        }
        return base | changes

    documents = [
        document(
            10,
            1,
            correspondent=1,
            document_type=1,
            tags=[1, 2],
            storage_path=1,
            archive_serial_number=77,
            created="2024-05-01",
            custom_fields=[
                {"field": 1, "value": "Hello"},
                {"field": 2, "value": "Some long text"},
                {"field": 3, "value": "https://example.org/x"},
                {"field": 4, "value": "2024-06-30"},
                {"field": 5, "value": True},
                {"field": 6, "value": 42},
                {"field": 7, "value": 1.5},
                {"field": 8, "value": "12.50"},
                {"field": 9, "value": "EUR99.90"},
                {"field": 10, "value": "b2"},
                {"field": 11, "value": [11]},
            ],
            notes=[
                {
                    "id": 1,
                    "note": "Second",
                    "created": "2024-02-02T10:00:00Z",
                    "user": {"username": "anna"},
                },
                {
                    "id": 2,
                    "note": "First",
                    "created": "2024-01-01T10:00:00Z",
                    "user": {"username": "tobias"},
                },
            ],
        ),
        document(
            11, 1, correspondent=2, tags=[2], custom_fields=[{"field": 3, "value": "not a url"}]
        ),
        document(12, 2, correspondent=1),
        document(
            13,
            2,
            permissions={
                "view": {"users": [3], "groups": [1]},
                "change": {"users": [1], "groups": []},
            },
        ),
        document(14, None),
        document(15, 99),
        document(
            16,
            1,
            permissions={
                "view": {"users": [4, 77], "groups": []},
                "change": {"users": [], "groups": []},
            },
        ),
        document(17, 1, original_file_name="mail.eml", mime_type="message/rfc822"),
        document(18, 1, deleted_at="2024-07-01T00:00:00Z"),
        document(
            19,
            1,
            custom_fields=[
                {"field": 8, "value": "EUR-5.00"},
                {"field": 10, "value": "zz"},
                {"field": 6, "value": None},
            ],
        ),
    ]
    files = {int(d["id"]): pdf(int(d["id"]), base_pdf) for d in documents}
    if jpeg is not None:
        documents[2]["original_file_name"] = "photo.jpg"
        documents[2]["mime_type"] = "image/jpeg"
        files[12] = jpeg
    return Archive(
        users=users,
        groups=[{"id": 1, "name": "Everyone"}],
        correspondents=[
            {"id": 1, "name": "ACME Energy", "matching_algorithm": 6, "owner": None},
            {"id": 2, "name": "acme energy", "matching_algorithm": 0, "owner": 1},
            {"id": 3, "name": "Unused", "matching_algorithm": 0, "owner": None},
        ],
        document_types=[{"id": 1, "name": "Invoice", "matching_algorithm": 0, "owner": None}],
        tags=[
            {"id": 1, "name": "tax", "parent": None, "is_inbox_tag": True, "matching_algorithm": 0},
            {
                "id": 2,
                "name": "tax/2024",
                "parent": 1,
                "is_inbox_tag": False,
                "matching_algorithm": 0,
            },
        ],
        custom_fields=fields,
        storage_paths=[{"id": 1, "name": "Bank", "path": "{{ title }}"}],
        documents=documents,
        saved_views=2,
        workflows=1,
        files=files,
    )

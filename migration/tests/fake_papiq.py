"""A small stand-in for the Papiq API: the endpoints the migration uses, in memory. The real API
is exercised by the flow tests in the backend's test suite."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any, cast
from uuid import uuid4

import httpx2

TOKEN = "papiq-test-key"
PREFIX = "/api/v1"
ADMIN: dict[str, Any] = {"id": "admin-id", "username": "root", "role": "admin", "active": True}


@dataclass
class FakePapiq:
    users: dict[str, dict[str, Any]] = field(default_factory=lambda: {ADMIN["id"]: dict(ADMIN)})
    contacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    document_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags: dict[str, dict[str, Any]] = field(default_factory=dict)
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    drawers: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    scope: str = "read_write"
    role: str = "admin"
    ticks: int = 1  # polls until a document's pipeline is through
    lanes: dict[str, str] = field(default_factory=dict)  # file name -> lane (default green)
    errors: dict[str, tuple[int, int]] = field(default_factory=dict)  # file name -> (status, times)
    log: list[tuple[str, str]] = field(default_factory=list)
    uploads: list[dict[str, str]] = field(default_factory=list)
    after_upload: Callable[[int], None] | None = None
    _polls: dict[str, int] = field(default_factory=dict)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def writes(self) -> list[tuple[str, str]]:
        return [entry for entry in self.log if entry[0] != "GET"]

    # --- routing -----------------------------------------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.log.append((request.method, request.url.path))
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return _problem(401, "authentication is required")
        path = request.url.path.removeprefix(PREFIX)
        method = request.method
        if path == "/auth/me":
            return httpx2.Response(
                200,
                json={"user": {**ADMIN, "role": self.role}, "token_scope": self.scope},
            )
        if method != "GET" and self.scope != "read_write":
            return _problem(403, "this API token may only read")
        body = (
            json.loads(request.content)
            if request.content and "json" in request.headers.get("content-type", "")
            else {}
        )
        simple = {
            "/users": self.users,
            "/contacts": self.contacts,
            "/document-types": self.document_types,
            "/tags": self.tags,
            "/attributes": self.attributes,
            "/drawers": self.drawers,
        }
        if path in simple:
            return self._collection(path, simple[path], method, body)
        if found := re.fullmatch(r"/(users|attributes)/([^/]+)", path):
            store = self.users if found[1] == "users" else self.attributes
            if method == "PATCH" and found[2] in store:
                if "active" in body:
                    store[found[2]]["active"] = body["active"]
                if "choices" in body:
                    store[found[2]]["choices"] = body["choices"]
                return httpx2.Response(200, json=store[found[2]])
        if found := re.fullmatch(r"/drawers/([^/]+)/shares/([^/]+)", path):
            drawer = self.drawers[found[1]]
            drawer["shares"] = [s for s in drawer["shares"] if s["user_id"] != found[2]]
            drawer["shares"].append({"user_id": found[2], "level": body["level"]})
            return httpx2.Response(200, json=drawer)
        if path == "/documents" and method == "POST":
            return self._upload(request)
        if path == "/documents" and method == "GET":
            items = list(self.documents.values())
            return httpx2.Response(200, json={"items": items, "next_cursor": None})
        if found := re.fullmatch(r"/documents/([^/]+)(/log)?", path):
            document = self.documents.get(found[1])
            if document is None:
                return _problem(404, "document not found")
            if found[2]:
                return httpx2.Response(200, json=document["_log"])
            return httpx2.Response(200, json=self._poll(document))
        return _problem(404, f"unknown {method} {path}")

    def _collection(
        self, path: str, store: dict[str, dict[str, Any]], method: str, body: dict[str, Any]
    ) -> httpx2.Response:
        if method == "GET":
            return httpx2.Response(200, json=list(store.values()))
        if method != "POST":
            return _problem(405, "method not allowed")
        name = str(body.get("username") or body.get("name"))
        if any(
            (item.get("username") or item["name"]).casefold() == name.casefold()
            for item in store.values()
        ):
            return _problem(409, f"{name} exists")
        id = uuid4().hex
        if path == "/users":
            item = {"id": id, "username": name, "role": "user", "active": True}
        elif path == "/drawers":
            item = {
                "id": id,
                "name": name,
                "owner_id": body.get("owner_id", ADMIN["id"]),
                "is_default": False,
                "shares": [],
            }
        elif path == "/attributes":
            item = {
                "id": id,
                "name": name,
                "data_type": body["data_type"],
                "choices": body.get("choices", []),
                "document_type_ids": None,
            }
        else:
            item = {"id": id, "name": name}
        store[id] = item
        return httpx2.Response(201, json=item)

    # --- documents ---------------------------------------------------------------------------

    def _upload(self, request: httpx2.Request) -> httpx2.Response:
        message = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: "
            + request.headers["content-type"].encode()
            + b"\r\n\r\n"
            + request.content
        )
        fields: dict[str, str] = {}
        content = b""
        filename = ""
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "file":
                filename = part.get_filename() or ""
                content = cast("bytes", part.get_payload(decode=True))
            else:
                fields[str(name)] = cast("bytes", part.get_payload(decode=True)).decode()
        self.uploads.append({**fields, "filename": filename})
        status, times = self.errors.get(filename, (0, 0))
        if times > 0:
            self.errors[filename] = (status, times - 1)
            return _problem(status, f"injected failure {status}")
        owner = fields.get("owner", ADMIN["id"])
        sha256 = hashlib.sha256(content).hexdigest()
        for existing in self.documents.values():
            if existing["owner_id"] == owner and existing["sha256"] == sha256:
                response = _problem(409, "you have this file already")
                body = json.loads(response.content)
                body["existing_document_id"] = existing["id"]
                return httpx2.Response(409, json=body)
        if not content.startswith(b"%PDF") and not content.startswith(b"\xff\xd8"):
            return _problem(415, "unsupported file type")
        metadata = json.loads(fields.get("metadata", "{}"))
        id = uuid4().hex
        self.documents[id] = {
            "id": id,
            "title": metadata.get("title", filename),
            "original_filename": filename,
            "media_type": "application/pdf",
            "channel": fields.get("channel", "api"),
            "sha256": sha256,
            "owner_id": owner,
            "drawer_id": fields.get("drawer_id", f"default:{owner}"),
            "contact_id": metadata.get("contact_id"),
            "document_type_id": metadata.get("document_type_id"),
            "tag_ids": sorted(metadata.get("tag_ids", [])),
            "document_date": metadata.get("document_date"),
            "attributes": metadata.get("attributes", {}),
            "lane": None,
            "processing": {"status": "processing", "current_step": "ocr", "run": 1, "outcomes": {}},
            "_lane": self.lanes.get(filename, "green"),
            "_log": [],
        }
        if self.after_upload is not None:
            self.after_upload(len(self.uploads))
        return httpx2.Response(202, json={"id": id, "status_url": f"{PREFIX}/documents/{id}"})

    def _poll(self, document: dict[str, Any]) -> dict[str, Any]:
        id = document["id"]
        self._polls[id] = self._polls.get(id, 0) + 1
        if self.ticks >= 0 and self._polls[id] > self.ticks:
            lane = document["_lane"]
            document["lane"] = lane
            document["processing"] = {
                "status": "completed" if lane == "green" else "review",
                "current_step": None,
                "run": 1,
                "outcomes": {},
            }
            if lane != "green":
                document["_log"] = [
                    {"step": "parse", "outcome": "failed", "reason": "no text recognised"}
                ]
        return {key: value for key, value in document.items() if not key.startswith("_")}


def _problem(status: int, detail: str) -> httpx2.Response:
    return httpx2.Response(
        status,
        json={"title": "error", "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )

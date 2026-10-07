"""The search index on Meilisearch, over its REST API with httpx2 (no SDK).

- Documents are written with `PUT /documents` (an update that sets every field it names; the
  vectors are only named when the document brings new ones). Meilisearch
  processes writes as tasks; every write here waits for its task, so a change is searchable when
  the call returns and a failed task raises `SearchIndexError` instead of getting lost.
- Settings (searchable, filterable and sortable attributes, languages, the embedder) are applied
  by the adapter itself, on first use and only when they differ from what the index has, so that
  a restart does not index again.
- Vectors are computed by Papiq and handed over (embedder `userProvided`, named `default`); a
  document may have several, one per section. A document without vectors opts out with `null`.
- The rights filter is part of every search and built from `Visibility`. Values in filters are
  ids and lane names only; the text of the user goes into `q`.
- A rebuild fills `<index>-rebuild` and replaces the active index with `swap-indexes`, so
  searches never see a half-built index.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, time
from time import monotonic
from typing import Any
from uuid import UUID

import httpx2

from papiq.core.domain.errors import SearchIndexError, SearchUnavailableError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.search import (
    EmbeddingStamp,
    IndexDocument,
    IndexState,
    Segment,
    Visibility,
    lane_value,
)
from papiq.core.ports.search_index import (
    IndexBuild,
    SearchHit,
    SearchQuery,
    SearchResult,
)

log = logging.getLogger(__name__)

EMBEDDER = "default"
HIGHLIGHT_START = "\ue000"  # private-use characters: they cannot occur in a document by chance
HIGHLIGHT_END = "\ue001"
CROP_WORDS = 30
FETCH_PAGE = 1000
_EXCERPT = 300  # characters of an error message from Meilisearch

SEARCHABLE = ["title", "contact", "document_type", "tags", "attributes", "filename", "text"]
FILTERABLE = ["owner_id", "drawer_id", "lane", "contact_id", "document_type_id", "tag_ids"]
SORTABLE = ["document_date", "created_at"]
STATE_FIELDS = ["id", "version", "embedding_model", "embedding_digest"]
MAX_TOTAL_HITS = 1000
READY_FOR = 30.0  # seconds an index counts as set up before its settings are looked at again
_UNORDERED = {"filterableAttributes", "sortableAttributes"}


class MeilisearchIndex:
    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        index: str = "papiq-documents",
        dimensions: int | None = None,
        locales: Sequence[str] = ("deu", "eng"),
        timeout: float = 30.0,
        task_timeout: float = 120.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """`dimensions`: the length of the vectors; without it the index has no embedder and
        takes no vectors. `transport` replaces the network in tests."""
        self._index = index
        self._build_index = f"{index}-rebuild"
        self._dimensions = dimensions
        self._locales = list(locales)
        self._task_timeout = task_timeout
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx2.AsyncClient(
            base_url=url, headers=headers, timeout=timeout, transport=transport
        )
        self._ready_until = 0.0  # monotonic time until which the index counts as set up
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- port -----------------------------------------------------------------------------------

    async def upsert(self, documents: Sequence[IndexDocument]) -> None:
        if documents:
            await self._ensure_ready()
            await self._write(self._index, documents)

    async def remove(self, id: DocumentId) -> None:
        await self._ensure_ready()
        task = await self._request("DELETE", f"/indexes/{self._index}/documents/{id}")
        await self._wait(task)

    async def state(self, id: DocumentId) -> IndexState | None:
        await self._ensure_ready()
        response = await self._send(
            "GET",
            f"/indexes/{self._index}/documents/{id}",
            params={"fields": ",".join(STATE_FIELDS)},
        )
        if response.status_code == 404:
            return None
        return _state_of(_json(_checked(response)))

    async def states(self) -> AsyncIterator[IndexState]:
        await self._ensure_ready()
        offset = 0
        while True:
            page = await self._request(
                "POST",
                f"/indexes/{self._index}/documents/fetch",
                json={"fields": STATE_FIELDS, "limit": FETCH_PAGE, "offset": offset},
            )
            results = page.get("results", [])
            for item in results:
                yield _state_of(item)
            offset += len(results)
            if len(results) < FETCH_PAGE or offset >= int(page.get("total", offset)):
                return

    async def search(self, query: SearchQuery) -> SearchResult:
        await self._ensure_ready()
        if query.filter.lanes is not None and not query.filter.lanes:
            return SearchResult(hits=[], estimated_total=0, semantic=False)
        body: dict[str, Any] = {
            "q": query.text,
            "filter": filter_expression(query),
            "offset": query.offset,
            "limit": query.limit,
            "attributesToRetrieve": ["id", "version"],
            "attributesToHighlight": ["text"],
            "attributesToCrop": ["text"],
            "cropLength": CROP_WORDS,
            "highlightPreTag": HIGHLIGHT_START,
            "highlightPostTag": HIGHLIGHT_END,
            "showRankingScore": True,
        }
        semantic = query.vector is not None and query.semantic_ratio > 0
        if semantic:
            if self._dimensions is None:
                raise SearchIndexError("the index has no embedder, so it takes no vectors")
            body["vector"] = list(query.vector or ())
            body["hybrid"] = {"embedder": EMBEDDER, "semanticRatio": query.semantic_ratio}
        data = await self._request("POST", f"/indexes/{self._index}/search", json=body)
        try:
            hits = [_hit_of(item) for item in data["hits"]]
            total = int(data.get("estimatedTotalHits", data.get("totalHits", 0)))
        except (KeyError, TypeError, ValueError):
            raise SearchIndexError("Meilisearch sent an unusable search result") from None
        return SearchResult(
            hits=hits, estimated_total=max(total, query.offset + len(hits)), semantic=semantic
        )

    async def begin_rebuild(self) -> IndexBuild:
        await self._ensure_ready()
        await self._delete_index(self._build_index)
        await self._prepare(self._build_index)
        return _MeilisearchBuild(self)

    async def check(self) -> None:
        response = await self._send("GET", "/health")
        if response.status_code != 200 or _json(response).get("status") != "available":
            raise SearchUnavailableError("Meilisearch is not available")
        await self._request("GET", "/version")  # also proves that the key is accepted

    # --- rebuild (used by _MeilisearchBuild) ----------------------------------------------------

    async def _write(self, index: str, documents: Sequence[IndexDocument]) -> None:
        if self._dimensions is not None:
            for document in documents:
                for vector in document.vectors or ():
                    if len(vector) != self._dimensions:
                        raise SearchIndexError(
                            f"document {document.id}: vector of length {len(vector)}, "
                            f"the index needs {self._dimensions}"
                        )
        body = [self._payload(document) for document in documents]
        # An update: fields that are left out stay as they are (the vectors of `vectors=None`).
        task = await self._request(
            "PUT", f"/indexes/{index}/documents", json=body, params={"primaryKey": "id"}
        )
        await self._wait(task)

    async def _swap_in_build(self) -> None:
        task = await self._request(
            "POST", "/swap-indexes", json=[{"indexes": [self._index, self._build_index]}]
        )
        await self._wait(task)
        await self._delete_index(self._build_index)  # now holds the old documents

    async def _delete_index(self, index: str) -> None:
        """Delete the index; Meilisearch accepts that for a missing one and fails the task."""
        try:
            task = await self._request("DELETE", f"/indexes/{index}")
            await self._wait(task)
        except _TaskFailedError as failure:
            if failure.code != "index_not_found":
                raise

    # --- preparation ----------------------------------------------------------------------------

    async def _ensure_ready(self) -> None:
        """Create the index and apply the settings if needed. Checked again every `READY_FOR`
        seconds and after an answer "index not found", so a Meilisearch that lost its data
        (a new volume) is set up again and the reconciliation can fill it."""
        if monotonic() < self._ready_until:
            return
        async with self._lock:
            if monotonic() >= self._ready_until:
                await self._prepare(self._index)
                self._ready_until = monotonic() + READY_FOR

    async def _prepare(self, index: str) -> None:
        """Create the index if it is missing and bring its settings to the wanted ones."""
        response = await self._send("GET", f"/indexes/{index}/settings")
        if response.status_code == 404:
            created = await self._request(
                "POST", "/indexes", json={"uid": index, "primaryKey": "id"}
            )
            await self._wait(created)
            current: Mapping[str, Any] = {}
        else:
            current = _json(_checked(response))
        wanted = self._settings()
        changed = {
            key: value
            for key, value in wanted.items()
            if _normalised(key, current.get(key)) != _normalised(key, value)
        }
        if changed:
            log.info(
                "applying search index settings", extra={"index": index, "keys": list(changed)}
            )
            task = await self._request("PATCH", f"/indexes/{index}/settings", json=changed)
            await self._wait(task)

    def _settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "searchableAttributes": SEARCHABLE,
            "filterableAttributes": FILTERABLE,
            "sortableAttributes": SORTABLE,
            "localizedAttributes": [{"locales": self._locales, "attributePatterns": ["*"]}],
            "pagination": {"maxTotalHits": MAX_TOTAL_HITS},
        }
        if self._dimensions is not None:
            settings["embedders"] = {
                EMBEDDER: {"source": "userProvided", "dimensions": self._dimensions}
            }
        return settings

    def _payload(self, document: IndexDocument) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(document.id),
            "version": document.version,
            "owner_id": str(document.owner_id),
            "drawer_id": str(document.drawer_id),
            "lane": document.lane_value,
            "title": document.title,
            "filename": document.filename,
            "text": document.text,
            "contact_id": _text(document.contact_id),
            "contact": document.contact,
            "document_type_id": _text(document.document_type_id),
            "document_type": document.document_type,
            "tag_ids": [str(tag) for tag in document.tag_ids],
            "tags": list(document.tags),
            "attributes": list(document.attributes),
            "document_date": None
            if document.document_date is None
            else int(datetime.combine(document.document_date, time(), UTC).timestamp()),
            "created_at": int(document.created_at.timestamp()),
        }
        if document.vectors is not None:
            payload["embedding_model"] = _stamp(document, "model")
            payload["embedding_digest"] = _stamp(document, "digest")
            if self._dimensions is not None:
                vectors = [list(vector) for vector in document.vectors]
                payload["_vectors"] = {EMBEDDER: vectors or None}
        return payload

    # --- HTTP -----------------------------------------------------------------------------------

    async def _send(self, method: str, path: str, **arguments: Any) -> httpx2.Response:
        try:
            response = await self._client.request(method, path, **arguments)
        except httpx2.TimeoutException:
            raise SearchUnavailableError("Meilisearch did not answer in time") from None
        except httpx2.HTTPError as failure:
            raise SearchUnavailableError(
                f"cannot reach Meilisearch: {type(failure).__name__}"
            ) from None
        if response.status_code >= 500:
            raise SearchUnavailableError(f"Meilisearch: HTTP {response.status_code}")
        return response

    async def _request(self, method: str, path: str, **arguments: Any) -> dict[str, Any]:
        response = await self._send(method, path, **arguments)
        if response.status_code == 404 and _code(response) == "index_not_found":
            self._ready_until = 0.0
        return _json(_checked(response))

    async def _wait(self, task: Mapping[str, Any]) -> None:
        """Wait for the task that a write answered with; raise if it failed."""
        try:
            uid = int(task["taskUid"])
        except (KeyError, TypeError, ValueError):
            raise SearchIndexError("Meilisearch sent no task") from None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._task_timeout
        pause = 0.02
        while True:
            data = await self._request("GET", f"/tasks/{uid}")
            status = data.get("status")
            if status == "succeeded":
                return
            if status in ("failed", "canceled"):
                error = data.get("error") or {}
                code, message = error.get("code", status), error.get("message", "")
                raise _TaskFailedError(uid, status, code, _shorten(message))
            if loop.time() >= deadline:
                raise SearchUnavailableError(
                    f"task {uid} did not finish within {self._task_timeout:g} seconds"
                )
            await asyncio.sleep(pause)
            pause = min(pause * 2, 0.5)


class _TaskFailedError(SearchIndexError):
    def __init__(self, uid: int, status: str, code: str, message: str) -> None:
        super().__init__(f"task {uid} {status}: {code}: {message}")
        self.code = code


class _MeilisearchBuild:
    def __init__(self, index: MeilisearchIndex) -> None:
        self._index = index

    async def add(self, documents: Sequence[IndexDocument]) -> None:
        if documents:
            await self._index._write(self._index._build_index, documents)

    async def finish(self) -> None:
        await self._index._swap_in_build()

    async def abort(self) -> None:
        await self._index._delete_index(self._index._build_index)


def filter_expression(query: SearchQuery) -> str:
    """The Meilisearch filter for the rights of the user and the criteria of the query. Only ids
    and lane names go in, both quoted."""
    parts = [_visibility_filter(query.visibility)]
    criteria = query.filter
    if criteria.contact is not None:
        parts.append(f"contact_id = {_quote(criteria.contact)}")
    if criteria.document_type is not None:
        parts.append(f"document_type_id = {_quote(criteria.document_type)}")
    parts.extend(f"tag_ids = {_quote(tag)}" for tag in sorted(criteria.tags, key=str))
    if criteria.drawer is not None:
        parts.append(f"drawer_id = {_quote(criteria.drawer)}")
    if criteria.lanes:
        lanes = sorted(lane_value(lane) for lane in criteria.lanes)
        parts.append(f"lane IN [{', '.join(_quote(lane) for lane in lanes)}]")
    return " AND ".join(parts)


def _visibility_filter(visibility: Visibility) -> str:
    own = f"owner_id = {_quote(visibility.user)}"
    if not visibility.drawers:
        return f"({own})"
    drawers = ", ".join(_quote(drawer) for drawer in sorted(visibility.drawers, key=str))
    return f'({own} OR (lane = "green" AND drawer_id IN [{drawers}]))'


def _normalised(key: str, setting: Any) -> Any:
    """Meilisearch keeps the sets of filterable and sortable attributes in its own order."""
    if key in _UNORDERED and isinstance(setting, list):
        return sorted(setting)
    return setting


def _quote(value: object) -> str:
    return json.dumps(str(value))


def _stamp(document: IndexDocument, part: str) -> str | None:
    return None if document.embedding is None else str(getattr(document.embedding, part))


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _checked(response: httpx2.Response) -> httpx2.Response:
    if response.status_code >= 400:
        raise SearchIndexError(f"Meilisearch: HTTP {response.status_code}: {_message(response)}")
    return response


def _json(response: httpx2.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise SearchIndexError("Meilisearch sent no JSON") from None
    if not isinstance(data, dict):
        raise SearchIndexError("Meilisearch sent no JSON object")
    return data


def _code(response: httpx2.Response) -> str | None:
    try:
        return str(response.json()["code"])
    except (ValueError, KeyError, TypeError):
        return None


def _message(response: httpx2.Response) -> str:
    try:
        data = response.json()
        text = f"{data['code']}: {data['message']}"
    except (ValueError, KeyError, TypeError):
        text = response.text
    return _shorten(text) or response.reason_phrase


def _shorten(text: str) -> str:
    return " ".join(text.split())[:_EXCERPT]


def _state_of(item: Mapping[str, Any]) -> IndexState:
    try:
        model, digest = item.get("embedding_model"), item.get("embedding_digest")
        return IndexState(
            id=DocumentId(_uuid(item["id"])),
            version=int(item["version"]),
            embedding=EmbeddingStamp(str(model), str(digest)) if model and digest else None,
        )
    except (KeyError, TypeError, ValueError):
        raise SearchIndexError("Meilisearch sent an unusable document") from None


def _hit_of(item: Mapping[str, Any]) -> SearchHit:
    formatted = item.get("_formatted") or {}
    score = item.get("_rankingScore")
    return SearchHit(
        id=DocumentId(_uuid(item["id"])),
        version=int(item["version"]),
        score=float(score) if isinstance(score, int | float) else None,
        snippet=segments(str(formatted.get("text") or "")),
    )


def segments(marked: str) -> tuple[Segment, ...]:
    """Split text marked with the highlight characters into segments."""
    result: list[Segment] = []
    position = 0
    while position < len(marked):
        start = marked.find(HIGHLIGHT_START, position)
        if start == -1:
            result.append(Segment(marked[position:]))
            break
        if start > position:
            result.append(Segment(marked[position:start]))
        end = marked.find(HIGHLIGHT_END, start + 1)
        end = len(marked) if end == -1 else end
        if end > start + 1:
            result.append(Segment(marked[start + 1 : end], match=True))
        position = end + 1
    return tuple(result)


def _uuid(value: object) -> UUID:
    return UUID(str(value))

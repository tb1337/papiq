"""An in-memory search index: words by substring, vectors by cosine similarity.

Simple on purpose: it applies the same rights filter and criteria as the real index and ranks
roughly alike, but it knows no typos, stemming or compound words beyond substrings.
"""

import math
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

from papiq.core.domain.errors import SearchIndexError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.search import IndexDocument, IndexState, Segment
from papiq.core.ports.search_index import (
    IndexBuild,
    SearchHit,
    SearchQuery,
    SearchResult,
)

_WORD = re.compile(r"\w+")
_SNIPPET_BEFORE = 60
_SNIPPET_LENGTH = 160
# Weight of a match by field: the title counts most, the text least.
_FIELD_WEIGHTS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4)


class MemorySearchIndex:
    def __init__(self, *, dimensions: int | None = None) -> None:
        """`dimensions`: refuse vectors of another length, as a real index does."""
        self._dimensions = dimensions
        self._documents: dict[DocumentId, IndexDocument] = {}

    async def upsert(self, documents: Sequence[IndexDocument]) -> None:
        _check(documents, self._dimensions)
        for document in documents:
            self._documents[document.id] = _keeping_vectors(
                document, self._documents.get(document.id)
            )

    async def remove(self, id: DocumentId) -> None:
        self._documents.pop(id, None)

    async def state(self, id: DocumentId) -> IndexState | None:
        document = self._documents.get(id)
        return None if document is None else _state(document)

    async def states(self) -> AsyncIterator[IndexState]:
        for document in list(self._documents.values()):
            yield _state(document)

    async def search(self, query: SearchQuery) -> SearchResult:
        terms = [word.casefold() for word in _WORD.findall(query.text)]
        use_vector = query.vector is not None and query.semantic_ratio > 0
        scored: list[tuple[float, IndexDocument]] = []
        for document in self._documents.values():
            if not query.visibility.allows(document) or not _matches_filter(document, query):
                continue
            words = _word_score(document, terms)
            similarity = _similarity(document, query.vector) if use_vector else None
            if words == 0 and similarity is None:
                continue
            if use_vector:
                score = (1 - query.semantic_ratio) * words + query.semantic_ratio * (
                    similarity or 0
                )
            else:
                score = words
            scored.append((score, document))
        scored.sort(key=lambda item: (-item[0], str(item[1].id)))
        page = scored[query.offset : query.offset + query.limit]
        hits = [
            SearchHit(
                id=document.id,
                version=document.version,
                score=score,
                snippet=_snippet(document.text, terms),
            )
            for score, document in page
        ]
        return SearchResult(hits=hits, estimated_total=len(scored), semantic=use_vector)

    async def begin_rebuild(self) -> IndexBuild:
        return _MemoryBuild(self)

    async def check(self) -> None:
        return None

    def replace_all(self, documents: dict[DocumentId, IndexDocument]) -> None:
        self._documents = documents

    @property
    def documents(self) -> dict[DocumentId, IndexDocument]:
        """What the index holds (for tests)."""
        return dict(self._documents)


class _MemoryBuild:
    def __init__(self, index: MemorySearchIndex) -> None:
        self._index = index
        self._documents: dict[DocumentId, IndexDocument] = {}
        self._closed = False

    async def add(self, documents: Sequence[IndexDocument]) -> None:
        self._open()
        _check(documents, self._index._dimensions)
        for document in documents:
            self._documents[document.id] = _keeping_vectors(document, None)

    async def finish(self) -> None:
        self._open()
        self._closed = True
        self._index.replace_all(self._documents)

    async def abort(self) -> None:
        self._closed = True
        self._documents = {}

    def _open(self) -> None:
        if self._closed:
            raise SearchIndexError("the rebuild is over")


def _keeping_vectors(document: IndexDocument, old: IndexDocument | None) -> IndexDocument:
    if document.vectors is not None:
        return document
    if old is None:
        raise SearchIndexError(f"document {document.id}: no vectors to keep")
    return replace(document, vectors=old.vectors, embedding=old.embedding)


def _check(documents: Sequence[IndexDocument], dimensions: int | None) -> None:
    if dimensions is None:
        return
    for document in documents:
        for vector in document.vectors or ():
            if len(vector) != dimensions:
                raise SearchIndexError(
                    f"document {document.id}: vector of length {len(vector)}, "
                    f"the index needs {dimensions}"
                )


def _state(document: IndexDocument) -> IndexState:
    return IndexState(id=document.id, version=document.version, embedding=document.embedding)


def _fields(document: IndexDocument) -> list[str]:
    """The searchable fields, in the order of `_FIELD_WEIGHTS`."""
    return [
        document.title,
        document.contact or "",
        document.document_type or "",
        " ".join(document.tags),
        " ".join(document.fields),
        document.filename,
        document.text,
    ]


def _word_score(document: IndexDocument, terms: list[str]) -> float:
    """0 unless every term occurs somewhere; else the mean weight of the best field per term."""
    if not terms:
        return 0.0
    fields = [field.casefold() for field in _fields(document)]
    total = 0.0
    for term in terms:
        weights = [w for field, w in zip(fields, _FIELD_WEIGHTS, strict=True) if term in field]
        if not weights:
            return 0.0
        total += max(weights)
    return total / len(terms)


def _similarity(document: IndexDocument, vector: tuple[float, ...] | None) -> float | None:
    """The best cosine similarity of a section to the query, mapped to 0..1; None without
    vectors."""
    if vector is None or not document.vectors:
        return None
    best = max(_cosine(vector, other) for other in document.vectors)
    return (best + 1) / 2


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return -1.0
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm


def _matches_filter(document: IndexDocument, query: SearchQuery) -> bool:
    criteria = query.filter
    if criteria.contact is not None and document.contact_id != criteria.contact:
        return False
    if criteria.document_type is not None and document.document_type_id != criteria.document_type:
        return False
    if not criteria.tags <= set(document.tag_ids):
        return False
    if criteria.drawer is not None and document.drawer_id != criteria.drawer:
        return False
    return criteria.lanes is None or document.lane in criteria.lanes


def _snippet(text: str, terms: list[str]) -> tuple[Segment, ...]:
    """About 160 characters of the text around the first match, matches marked."""
    if not text:
        return ()
    spans = _match_spans(text, terms)
    start = max(0, spans[0][0] - _SNIPPET_BEFORE) if spans else 0
    end = min(len(text), start + _SNIPPET_LENGTH)
    segments: list[Segment] = []
    position = start
    for begin, finish in spans:
        if finish <= position or begin >= end:
            continue
        begin, finish = max(begin, position), min(finish, end)
        if begin > position:
            segments.append(Segment(text[position:begin]))
        segments.append(Segment(text[begin:finish], match=True))
        position = finish
    if position < end:
        segments.append(Segment(text[position:end]))
    return tuple(segments)


def _match_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    lowered = text.casefold()
    if len(lowered) != len(text):  # case folding changed the length: positions would shift
        lowered = text.lower()
    spans: list[tuple[int, int]] = []
    for term in terms:
        position = lowered.find(term)
        while position != -1 and len(lowered) == len(text):
            spans.append((position, position + len(term)))
            position = lowered.find(term, position + len(term))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for begin, finish in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged

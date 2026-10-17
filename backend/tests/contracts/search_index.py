"""Contract of the search index. Needs the fixture `search_index`: an empty index that takes
vectors of three numbers (`DIMENSIONS`)."""

from datetime import date

import pytest

from papiq.core.domain.errors import SearchIndexError
from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    FieldId,
    SpaceId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.search import EmbeddingStamp, IndexDocument, Visibility
from papiq.core.ports import DocumentFilter, DocumentOrder, SearchIndex, SearchQuery, SortKey
from papiq.core.ports.repository import TagSets
from papiq.core.ports.search_index import SimilarQuery
from tests.builders import index_document

DIMENSIONS = 3
STAMP = EmbeddingStamp("test-model", "abc")


def visibility(user: UserId, *spaces: SpaceId) -> Visibility:
    return Visibility(user, frozenset(spaces))


def query(text: str, who: Visibility, **fields: object) -> SearchQuery:
    return SearchQuery(text=text, visibility=who, **fields)  # type: ignore[arg-type]


def ids(result_hits: list) -> set[DocumentId]:  # type: ignore[type-arg]
    return {hit.id for hit in result_hits}


async def found(
    index: SearchIndex, text: str, who: Visibility, **fields: object
) -> set[DocumentId]:
    return ids((await index.search(query(text, who, **fields))).hits)


class SearchIndexContract:
    # --- writing --------------------------------------------------------------------------------

    async def test_state_of_an_indexed_document(self, search_index: SearchIndex) -> None:
        document = index_document(version=3, vectors=((1.0, 0.0, 0.0),), embedding=STAMP)
        plain = index_document(version=1)
        await search_index.upsert([document, plain])
        state = await search_index.state(document.id)
        assert state is not None
        assert (state.id, state.version, state.embedding) == (document.id, 3, STAMP)
        plain_state = await search_index.state(plain.id)
        assert plain_state is not None
        assert plain_state.embedding is None
        assert await search_index.state(DocumentId(new_id())) is None

    async def test_upsert_replaces_a_document(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        document = index_document(owner_id=owner, title="Stromrechnung", version=1)
        await search_index.upsert([document])
        await search_index.upsert(
            [index_document(id=document.id, owner_id=owner, title="Mietvertrag", version=2)]
        )
        who = visibility(owner)
        assert await found(search_index, "Stromrechnung", who) == set()
        assert await found(search_index, "Mietvertrag", who) == {document.id}
        state = await search_index.state(document.id)
        assert state is not None
        assert state.version == 2

    async def test_vectors_can_be_kept_when_the_document_changes(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        document = index_document(
            owner_id=owner, title="Eins", vectors=((0.0, 1.0, 0.0),), embedding=STAMP, version=1
        )
        await search_index.upsert([document])
        await search_index.upsert(
            [index_document(id=document.id, owner_id=owner, title="Zwei", vectors=None, version=2)]
        )
        state = await search_index.state(document.id)
        assert state is not None
        assert (state.version, state.embedding) == (2, STAMP)
        assert await found(search_index, "Zwei", visibility(owner)) == {document.id}
        by_meaning = await search_index.search(
            query("zzz", visibility(owner), vector=(0.0, 1.0, 0.0), semantic_ratio=1.0)
        )
        assert by_meaning.hits[0].id == document.id

    async def test_vectors_can_be_dropped(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        document = index_document(
            owner_id=owner, title="Eins", vectors=((0.0, 1.0, 0.0),), embedding=STAMP
        )
        await search_index.upsert([document])
        await search_index.upsert(
            [index_document(id=document.id, owner_id=owner, title="Eins", vectors=())]
        )
        state = await search_index.state(document.id)
        assert state is not None
        assert state.embedding is None

    async def test_upsert_without_documents_does_nothing(self, search_index: SearchIndex) -> None:
        await search_index.upsert([])
        assert [state async for state in search_index.states()] == []

    async def test_remove(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        document = index_document(owner_id=owner, title="Mahnung")
        await search_index.upsert([document])
        await search_index.remove(document.id)
        await search_index.remove(document.id)  # not there any more: fine
        await search_index.remove(DocumentId(new_id()))
        assert await found(search_index, "Mahnung", visibility(owner)) == set()
        assert await search_index.state(document.id) is None

    async def test_states_list_every_document(self, search_index: SearchIndex) -> None:
        documents = [index_document(version=n) for n in range(1, 8)]
        await search_index.upsert(documents)
        states = {state.id: state.version async for state in search_index.states()}
        assert states == {document.id: document.version for document in documents}

    async def test_vectors_of_the_wrong_length_are_refused(self, search_index: SearchIndex) -> None:
        document = index_document(vectors=((1.0, 0.0),), embedding=STAMP)
        with pytest.raises(SearchIndexError):
            await search_index.upsert([document])

    # --- words ----------------------------------------------------------------------------------

    async def test_words_are_found_in_every_field(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        document = index_document(
            owner_id=owner,
            title="Jahresabrechnung",
            filename="scan0815.pdf",
            text="Der Zählerstand beträgt 4711 Kilowattstunden.",
            contact="Stadtwerke Musterstadt",
            document_type="Rechnung",
            tags=("Energie",),
            fields=("Rechnungsnummer: R-2026-77",),
        )
        other = index_document(owner_id=owner, title="Unrelated")
        await search_index.upsert([document, other])
        who = visibility(owner)
        for word in (
            "Jahresabrechnung",
            "scan0815",
            "Kilowattstunden",
            "Stadtwerke",
            "Rechnung",
            "Energie",
            "R-2026-77",
        ):
            assert await found(search_index, word, who) == {document.id}, word

    async def test_case_does_not_matter(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        document = index_document(owner_id=owner, title="Mietvertrag")
        await search_index.upsert([document])
        assert await found(search_index, "MIETVERTRAG", visibility(owner)) == {document.id}

    async def test_no_match_no_hit(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        await search_index.upsert([index_document(owner_id=owner, title="Mietvertrag")])
        result = await search_index.search(query("Zahnarzt", visibility(owner)))
        assert result.hits == []
        assert result.estimated_total == 0
        assert not result.semantic

    async def test_query_text_is_text_not_syntax(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        other = UserId(new_id())
        await search_index.upsert([index_document(owner_id=other, title="Geheim")])
        for text in ['" OR owner_id != "', "lane = green", "*", "\\", "a'b\"c", "(("]:
            await search_index.search(query(text, visibility(owner)))  # no error
        assert await found(search_index, '" OR owner_id != "', visibility(owner)) == set()

    async def test_hits_carry_version_score_and_marked_snippet(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        text = "Sehr geehrte Damen und Herren, wir mahnen die offene Zahlung an. Mit Gruß"
        document = index_document(owner_id=owner, title="Brief", text=text, version=4)
        await search_index.upsert([document])
        result = await search_index.search(query("Zahlung", visibility(owner)))
        (hit,) = result.hits
        assert (hit.id, hit.version) == (document.id, 4)
        assert hit.score is None or 0 <= hit.score <= 1
        assert [segment.text for segment in hit.snippet if segment.match] == ["Zahlung"]
        shown = "".join(segment.text for segment in hit.snippet).strip("…")
        assert shown in text or text in shown or "Zahlung" in shown

    async def test_the_snippet_is_plain_text(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        text = "Vorsicht <script>alert(1)</script> bei Rechnung & Co"
        await search_index.upsert([index_document(owner_id=owner, title="x", text=text)])
        (hit,) = (await search_index.search(query("Rechnung", visibility(owner)))).hits
        shown = "".join(segment.text for segment in hit.snippet)
        assert "<script>" in shown  # as written, not interpreted: the client escapes
        assert [segment.text for segment in hit.snippet if segment.match] == ["Rechnung"]

    # --- filters --------------------------------------------------------------------------------

    async def test_filters(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        space_a, space_b = SpaceId(new_id()), SpaceId(new_id())
        contact, kind = ContactId(new_id()), DocumentTypeId(new_id())
        tag_1, tag_2 = TagId(new_id()), TagId(new_id())
        base = {"owner_id": owner, "text": "Rechnung"}
        both = index_document(
            **base, space_id=space_a, contact_id=contact, document_type_id=kind,
            tag_ids=(tag_1, tag_2), lane=Lane.GREEN,
        )  # fmt: skip
        one_tag = index_document(**base, space_id=space_b, tag_ids=(tag_1,), lane=Lane.YELLOW)
        processing = index_document(**base, space_id=space_b, lane=None)
        await search_index.upsert([both, one_tag, processing])
        who = visibility(owner)

        async def with_filter(**fields: object) -> set[DocumentId]:
            return await found(search_index, "Rechnung", who, filter=DocumentFilter(**fields))  # type: ignore[arg-type]

        assert await with_filter() == {both.id, one_tag.id, processing.id}
        assert await with_filter(contact=contact) == {both.id}
        assert await with_filter(document_type=kind) == {both.id}
        assert await with_filter(tags=(frozenset({tag_1}),)) == {both.id, one_tag.id}
        assert await with_filter(tags=(frozenset({tag_1}), frozenset({tag_2}))) == {both.id}
        assert await with_filter(tags=(frozenset({tag_2, TagId(new_id())}),)) == {both.id}
        assert await with_filter(space=space_b) == {one_tag.id, processing.id}
        assert await with_filter(lanes=frozenset({Lane.GREEN})) == {both.id}
        assert await with_filter(lanes=frozenset({Lane.GREEN, Lane.YELLOW})) == {
            both.id,
            one_tag.id,
        }
        assert await with_filter(lanes=frozenset({None})) == {processing.id}
        assert await with_filter(lanes=frozenset({None, Lane.RED})) == {processing.id}
        assert await with_filter(
            tags=(frozenset({tag_1}),), space=space_b, lanes=frozenset({Lane.YELLOW})
        ) == {one_tag.id}
        assert await with_filter(contact=ContactId(new_id())) == set()

    async def test_tags_with_their_ancestors_and_groups_are_found_by_name(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        grouped = index_document(
            owner_id=owner,
            text="Schreiben",
            tags=("Krankenversicherung", "Versicherungen"),
            contact_groups=("Versorger",),
            document_type_groups=("Belege",),
        )
        other = index_document(owner_id=owner, text="Schreiben")
        await search_index.upsert([grouped, other])
        who = visibility(owner)
        for word in ("Versicherungen", "Versorger", "Belege"):
            assert await found(search_index, word, who) == {grouped.id}
        assert await found(search_index, "Schreiben", who) == {grouped.id, other.id}

    async def test_groups_of_contact_and_type(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        insurer, employer, bills = (TagId(new_id()) for _ in range(3))
        both = index_document(
            owner_id=owner,
            text="Rechnung",
            contact_group_ids=(insurer, employer),
            document_type_group_ids=(bills,),
        )
        employed = index_document(owner_id=owner, text="Rechnung", contact_group_ids=(employer,))
        neither = index_document(owner_id=owner, text="Rechnung")
        await search_index.upsert([both, employed, neither])

        async def with_filter(contacts: TagSets = (), types: TagSets = ()) -> set[DocumentId]:
            filter = DocumentFilter(contact_groups=contacts, document_type_groups=types)
            return await found(search_index, "Rechnung", visibility(owner), filter=filter)

        assert await with_filter(contacts=(frozenset({employer}),)) == {both.id, employed.id}
        assert await with_filter(contacts=(frozenset({insurer}), frozenset({employer}))) == {
            both.id
        }
        assert await with_filter(types=(frozenset({bills}),)) == {both.id}
        assert await with_filter(types=(frozenset({insurer}),)) == set()

    async def test_a_topic_with_those_above_it(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        insurance, health, work = (TagId(new_id()) for _ in range(3))
        insured = index_document(
            owner_id=owner,
            text="Schreiben",
            topic_ids=(health, insurance),
            topics=("Krankenversicherung", "Versicherungen"),
        )
        working = index_document(owner_id=owner, text="Schreiben", topic_ids=(work,))
        neither = index_document(owner_id=owner, text="Schreiben")
        await search_index.upsert([insured, working, neither])
        who = visibility(owner)
        assert await found(search_index, "Versicherungen", who) == {insured.id}

        async def with_topics(*topics: TagId) -> set[DocumentId]:
            filter = DocumentFilter(topics=frozenset(topics))
            return await found(search_index, "Schreiben", who, filter=filter)

        assert await with_topics(insurance) == {insured.id}
        assert await with_topics(work, health) == {insured.id, working.id}
        assert await with_topics(TagId(new_id())) == set()

    async def test_a_field_with_a_value(self, search_index: SearchIndex) -> None:
        owner, field = UserId(new_id()), FieldId(new_id())
        with_value = index_document(owner_id=owner, text="Rechnung", field_ids=(field,))
        without = index_document(owner_id=owner, text="Rechnung")
        await search_index.upsert([with_value, without])
        assert await found(
            search_index, "Rechnung", visibility(owner), filter=DocumentFilter(field=field)
        ) == {with_value.id}

    async def test_sorted_by_date_and_title_without_a_date_last(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        march = index_document(
            owner_id=owner, title="Ärztebrief", text="Rechnung", document_date=date(2026, 3, 1)
        )
        december = index_document(
            owner_id=owner, title="bank", text="Rechnung", document_date=date(2025, 12, 24)
        )
        undated = index_document(owner_id=owner, title="Abrechnung", text="Rechnung")
        await search_index.upsert([march, december, undated])
        who = visibility(owner)

        async def ordered(order: DocumentOrder, **fields: object) -> list[DocumentId]:
            result = await search_index.search(query("Rechnung", who, order=order, **fields))
            return [hit.id for hit in result.hits]

        newest = DocumentOrder(SortKey.DOCUMENT_DATE)
        assert await ordered(newest) == [march.id, december.id, undated.id]
        oldest = DocumentOrder(SortKey.DOCUMENT_DATE, ascending=True)
        assert await ordered(oldest) == [december.id, march.id, undated.id]
        by_title = DocumentOrder(SortKey.TITLE, ascending=True)
        assert await ordered(by_title) == [undated.id, march.id, december.id]
        # Words and meaning together keep the order.
        if (await search_index.search(query("Rechnung", who, vector=(1.0, 0.0, 0.0)))).semantic:
            assert await ordered(newest, vector=(1.0, 0.0, 0.0), semantic_ratio=0.5) == [
                march.id,
                december.id,
                undated.id,
            ]

    # --- rights ---------------------------------------------------------------------------------

    async def test_rights_filter(self, search_index: SearchIndex) -> None:
        me, other = UserId(new_id()), UserId(new_id())
        mine, shared, foreign = SpaceId(new_id()), SpaceId(new_id()), SpaceId(new_id())

        def doc(owner: UserId, space: SpaceId, lane: Lane | None) -> IndexDocument:
            return index_document(owner_id=owner, space_id=space, lane=lane, text="Vertrag")

        own = {lane: doc(me, mine, lane) for lane in (None, Lane.GREEN, Lane.YELLOW, Lane.RED)}
        own_elsewhere = doc(me, foreign, Lane.RED)  # my document in a space I cannot see
        green_shared = doc(other, shared, Lane.GREEN)
        green_mine = doc(other, mine, Lane.GREEN)  # another user filed it into my space
        green_foreign = doc(other, foreign, Lane.GREEN)
        others_unfinished = [doc(other, shared, lane) for lane in (None, Lane.YELLOW, Lane.RED)]
        await search_index.upsert(
            [
                *own.values(),
                own_elsewhere,
                green_shared,
                green_mine,
                green_foreign,
                *others_unfinished,
            ]
        )

        expected = {
            *(document.id for document in own.values()),
            own_elsewhere.id,
            green_shared.id,
            green_mine.id,
        }
        assert await found(search_index, "Vertrag", visibility(me, mine, shared)) == expected
        # Without spaces only the own documents remain.
        assert await found(search_index, "Vertrag", visibility(me)) == {
            *(document.id for document in own.values()),
            own_elsewhere.id,
        }
        # A user with no documents and no spaces finds nothing.
        assert await found(search_index, "Vertrag", visibility(UserId(new_id()))) == set()
        # An admin who asks for everything finds every document, in every lane.
        everything = Visibility(UserId(new_id()), frozenset(), everything=True)
        assert await found(search_index, "Vertrag", everything) == {
            *expected,
            green_foreign.id,
            *(document.id for document in others_unfinished),
        }
        yellow = DocumentFilter(lanes=frozenset({Lane.YELLOW}))
        assert await found(search_index, "Vertrag", everything, filter=yellow) == {
            own[Lane.YELLOW].id,
            others_unfinished[1].id,
        }

    async def test_the_rights_filter_applies_with_every_other_filter(
        self, search_index: SearchIndex
    ) -> None:
        me, other = UserId(new_id()), UserId(new_id())
        space = SpaceId(new_id())
        contact = ContactId(new_id())
        hidden = index_document(
            owner_id=other, space_id=space, lane=Lane.YELLOW, contact_id=contact, text="Vertrag"
        )
        await search_index.upsert([hidden])
        who = visibility(me, space)
        fields = DocumentFilter(contact=contact, space=space, lanes=frozenset({Lane.YELLOW}))
        assert await found(search_index, "Vertrag", who, filter=fields) == set()

    # --- paging ---------------------------------------------------------------------------------

    async def test_paging(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        documents = [index_document(owner_id=owner, title=f"Rechnung {n}") for n in range(7)]
        await search_index.upsert(documents)
        who = visibility(owner)
        seen: list[DocumentId] = []
        for offset in (0, 3, 6):
            result = await search_index.search(query("Rechnung", who, offset=offset, limit=3))
            assert len(result.hits) == min(3, 7 - offset)
            assert result.estimated_total >= 7
            seen += [hit.id for hit in result.hits]
        assert sorted(seen) == sorted(document.id for document in documents)
        past_the_end = await search_index.search(query("Rechnung", who, offset=9, limit=3))
        assert past_the_end.hits == []

    # --- vectors --------------------------------------------------------------------------------

    async def test_vector_search_finds_by_meaning(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        near = index_document(
            owner_id=owner, title="Eins", vectors=((0.0, 1.0, 0.0),), embedding=STAMP
        )
        far = index_document(
            owner_id=owner, title="Zwei", vectors=((0.0, 0.0, 1.0),), embedding=STAMP
        )
        no_vector = index_document(owner_id=owner, title="Drei")
        await search_index.upsert([far, near, no_vector])
        result = await search_index.search(
            query("irgendwas", visibility(owner), vector=(0.0, 1.0, 0.0), semantic_ratio=1.0)
        )
        assert result.hits[0].id == near.id
        assert result.semantic

    async def test_any_section_may_match(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        sections = index_document(
            owner_id=owner,
            title="Lang",
            vectors=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            embedding=STAMP,
        )
        other = index_document(
            owner_id=owner, title="Anders", vectors=((0.0, 0.0, 1.0),), embedding=STAMP
        )
        await search_index.upsert([other, sections])
        result = await search_index.search(
            query("zzz", visibility(owner), vector=(0.0, 1.0, 0.0), semantic_ratio=1.0)
        )
        assert result.hits[0].id == sections.id

    async def test_hybrid_search_combines_words_and_vectors(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        by_word = index_document(owner_id=owner, title="Kündigung", text="Kündigung des Vertrags")
        by_meaning = index_document(
            owner_id=owner, title="Beendigung", vectors=((0.0, 1.0, 0.0),), embedding=STAMP
        )
        await search_index.upsert([by_word, by_meaning])
        who = visibility(owner)
        hybrid = await search_index.search(
            query("Kündigung", who, vector=(0.0, 1.0, 0.0), semantic_ratio=0.5)
        )
        assert ids(hybrid.hits) == {by_word.id, by_meaning.id}
        assert hybrid.semantic
        words_only = await search_index.search(
            query("Kündigung", who, vector=(0.0, 1.0, 0.0), semantic_ratio=0.0)
        )
        assert ids(words_only.hits) == {by_word.id}
        assert not words_only.semantic

    async def test_a_vector_never_shows_what_the_rights_hide(
        self, search_index: SearchIndex
    ) -> None:
        me, other = UserId(new_id()), UserId(new_id())
        secret = index_document(
            owner_id=other, vectors=((0.0, 1.0, 0.0),), embedding=STAMP, lane=Lane.GREEN
        )
        await search_index.upsert([secret])
        result = await search_index.search(
            query("irgendwas", visibility(me), vector=(0.0, 1.0, 0.0), semantic_ratio=1.0)
        )
        assert result.hits == []

    async def test_similar_documents(self, search_index: SearchIndex) -> None:
        """Nearest first, as the rights and the criteria allow, without the document itself;
        documents without vectors are not among them."""
        me, other = UserId(new_id()), UserId(new_id())
        shared = SpaceId(new_id())

        def vectored(vector: tuple[float, ...], **fields: object) -> IndexDocument:
            return index_document(vectors=(vector,), embedding=STAMP, **fields)

        itself = vectored((0.0, 1.0, 0.0), owner_id=me, confirmed=True)
        near = vectored((0.1, 0.9, 0.0), owner_id=me, confirmed=True)
        farther = vectored((0.5, 0.5, 0.0), owner_id=me, confirmed=True)
        unconfirmed = vectored((0.0, 1.0, 0.0), owner_id=me)
        hidden = vectored((0.0, 1.0, 0.0), owner_id=other, confirmed=True, lane=Lane.YELLOW)
        visible = vectored(
            (0.0, 0.0, 1.0), owner_id=other, space_id=shared, confirmed=True, lane=Lane.GREEN
        )
        plain = index_document(owner_id=me, confirmed=True)
        await search_index.upsert([itself, near, farther, unconfirmed, hidden, visible, plain])
        confirmed = DocumentFilter(confirmed=True)
        found = await search_index.similar(
            SimilarQuery(
                vector=(0.0, 1.0, 0.0),
                visibility=visibility(me, shared),
                filter=confirmed,
                exclude=itself.id,
                limit=3,
            )
        )
        assert [hit.id for hit in found] == [near.id, farther.id, visible.id]
        one = await search_index.similar(
            SimilarQuery(vector=(0.0, 1.0, 0.0), visibility=visibility(me), limit=1)
        )
        (nearest,) = one
        assert nearest.id in {itself.id, unconfirmed.id}

    # --- rebuild --------------------------------------------------------------------------------

    async def test_rebuild_replaces_the_index(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        old = index_document(owner_id=owner, title="Alt")
        kept = index_document(owner_id=owner, title="Bleibt", version=1)
        await search_index.upsert([old, kept])
        who = visibility(owner)

        build = await search_index.begin_rebuild()
        fresh = index_document(owner_id=owner, title="Neu")
        await build.add(
            [fresh, index_document(id=kept.id, owner_id=owner, title="Bleibt", version=2)]
        )
        # The active index serves until the new one is ready.
        assert await found(search_index, "Alt", who) == {old.id}
        assert await found(search_index, "Neu", who) == set()

        await build.finish()
        assert await found(search_index, "Alt", who) == set()
        assert await found(search_index, "Neu", who) == {fresh.id}
        state = await search_index.state(kept.id)
        assert state is not None
        assert state.version == 2

    async def test_an_aborted_rebuild_changes_nothing(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        old = index_document(owner_id=owner, title="Alt")
        await search_index.upsert([old])
        build = await search_index.begin_rebuild()
        await build.add([index_document(owner_id=owner, title="Neu")])
        await build.abort()
        who = visibility(owner)
        assert await found(search_index, "Alt", who) == {old.id}
        assert await found(search_index, "Neu", who) == set()

    async def test_a_new_rebuild_discards_the_remains_of_an_old_one(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        first = await search_index.begin_rebuild()
        await first.add([index_document(owner_id=owner, title="Erster")])
        second = await search_index.begin_rebuild()
        fresh = index_document(owner_id=owner, title="Zweiter")
        await second.add([fresh])
        await second.finish()
        who = visibility(owner)
        assert await found(search_index, "Erster", who) == set()
        assert await found(search_index, "Zweiter", who) == {fresh.id}

    async def test_a_rebuild_to_an_empty_index(self, search_index: SearchIndex) -> None:
        owner = UserId(new_id())
        await search_index.upsert([index_document(owner_id=owner, title="Alt")])
        build = await search_index.begin_rebuild()
        await build.finish()
        assert [state async for state in search_index.states()] == []

    async def test_writes_go_to_the_new_index_after_the_rebuild(
        self, search_index: SearchIndex
    ) -> None:
        owner = UserId(new_id())
        build = await search_index.begin_rebuild()
        await build.finish()
        document = index_document(owner_id=owner, title="Danach")
        await search_index.upsert([document])
        assert await found(search_index, "Danach", visibility(owner)) == {document.id}

    async def test_check(self, search_index: SearchIndex) -> None:
        await search_index.check()

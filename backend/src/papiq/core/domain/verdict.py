"""The judge's rules: contact, document type, topic and tags from what the signals proposed.

A signal *names* a value when it is sure of it:

- the language model: its sender matches a contact (name or alias, legal forms and
  punctuation aside) at least `accept` and no other contact comes close; a type or tag from the
  lists;
- the classifier: its best class with at least `classifier` probability (a tag: its own);
- the similar documents: at least two of them, and more than half of those found, carry it;
  a contact only if no other contact is carried by two of them as well (similar documents split
  between senders are alike by their form, a pay slip's employee for one, not by their sender).

Facts in the text name nothing on their own; they are evidence: an identifier of the contact,
or its name (or alias) standing whole in the text, is *hard* evidence. An identifier of a
contact alone makes it a candidate, never green.

A signal *contradicts* a value when it names another one; for a contact also when the model's
recipient is that contact, when an identifier of another contact stands in the text, and when
the model's sender is none of the contacts and unrelated to this one. A contradiction is
always uncertain, also against a value the other signal could not have named.

A signal *covers* a value when it could have named it: the classifier if it was trained on
the value, the similar documents if any was found and a confirmed document carries the
value.

- A contact is green without contradiction if hard evidence backs it and a signal names it, or
  if the model's sender matches it at least `suggest` and the classifier or the similar
  documents name it.
- A type is green without contradiction if two signals name it, or if only the model names it
  and no other signal covers it: the rule from before the signals, per value, until the
  others know it.
- A tag is set as a type is green; one only a single signal names is a suggestion. Tags never
  make a document uncertain. Only the tags the classification sets are judged (document tags
  set `auto`); a tag set by hand is never proposed.
- A topic is judged after the contact, among the topics that contact enables (those set
  `auto`): it is set if the contact is green and an identifier of the pair of contact and
  topic stands in the text and a signal names the topic, or two signals name it. Otherwise it
  is a suggestion; if signals (or identifiers of the pair) name different topics, it stays
  empty with all candidates. A topic never makes a document uncertain. Under a contact that is
  only suggested, the topic is only suggested too.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_TYPE,
    TAGS,
    TOPIC,
    Candidate,
    CandidateSource,
    FieldCheck,
)
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.names import best_match, mentions, name_key, related
from papiq.core.domain.pipeline import Node, Outcome
from papiq.core.domain.tag_tree import TagTree

LLM = Node.SIGNAL_LLM.value
CLASSIFIER = Node.SIGNAL_CLASSIFIER.value
NEIGHBOURS = Node.SIGNAL_NEIGHBOURS.value
FACTS = Node.SIGNAL_FACTS.value
_UNSURE = 0.89  # the confidence of an uncertain field stays below acceptance


@dataclass(frozen=True)
class Thresholds:
    accept: float = 0.9  # the model's sender matches a contact this well: it names it
    suggest: float = 0.75  # this well: a weak match, a suggestion
    classifier: float = 0.8  # the classifier this sure: it names the class
    ambiguity: float = 0.05  # two contacts this close: the model names neither


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class ModelProposal:
    """What the language model proposed (`signal_llm`)."""

    sender: str | None = None
    sender_quote: str | None = None
    recipient: str | None = None
    document_type: str | None = None
    new_document_type: str | None = None
    tags: tuple[str, ...] = ()
    new_tags: tuple[str, ...] = ()
    topic: str | None = None


@dataclass(frozen=True)
class ClassifierView:
    """The classifier's probabilities (`signal_classifier`), best first, and what it knows."""

    contacts: Sequence[tuple[ContactId, float]] = ()
    document_types: Sequence[tuple[DocumentTypeId, float]] = ()
    tags: Mapping[TagId, float] = field(default_factory=dict)
    known_contacts: frozenset[ContactId] = frozenset()
    known_types: frozenset[DocumentTypeId] = frozenset()
    topics: Sequence[tuple[TagId, float]] = ()
    known_topics: frozenset[TagId] = frozenset()


@dataclass(frozen=True)
class Neighbour:
    document: str
    contact: ContactId | None
    document_type: DocumentTypeId | None
    tags: frozenset[TagId] = frozenset()
    similarity: float | None = None  # to the document, as the search index measures it
    topic: TagId | None = None


@dataclass(frozen=True)
class FactsView:
    """Contacts named in the text, the kinds of their identifiers found in it, and the topics
    those identifiers point to (each with the kinds that point to it): the pairs of contact and
    topic the text shows."""

    named: frozenset[ContactId] = frozenset()
    identifiers: Mapping[ContactId, tuple[str, ...]] = field(default_factory=dict)
    topics: Mapping[ContactId, Mapping[TagId, tuple[str, ...]]] = field(default_factory=dict)


@dataclass(frozen=True)
class KnownLabels:
    """The contacts, types, topics and tags confirmed documents carry: what similar documents
    can name."""

    contacts: frozenset[ContactId] = frozenset()
    document_types: frozenset[DocumentTypeId] = frozenset()
    tags: frozenset[TagId] = frozenset()
    topics: frozenset[TagId] = frozenset()


@dataclass(frozen=True)
class Signals:
    """What each signal said; None where it abstained (or, before the signals, never ran).
    `neighbours_know`: what the similar documents could have named; None (an older entry)
    counts as everything."""

    model: ModelProposal | None = None
    classifier: ClassifierView | None = None
    neighbours: tuple[Neighbour, ...] | None = None
    facts: FactsView | None = None
    neighbours_know: KnownLabels | None = None


# --- contact ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ContactView:
    """What the signals say about contacts, worked out once."""

    names: Mapping[ContactId, Contact]
    sender: str | None
    quote: str | None
    matched: ContactId | None  # the contact the model's sender matches at least `suggest`
    match_score: float
    ambiguous: bool  # another contact matches the sender about as well
    model_names: bool
    recipient: ContactId | None
    probabilities: Mapping[ContactId, float]
    classifier_names: ContactId | None
    neighbours: tuple[Neighbour, ...]
    votes: Counter[ContactId]
    neighbours_name: ContactId | None
    facts: FactsView
    proposers: Mapping[ContactId, tuple[str, ...]]
    thresholds: Thresholds

    def hard(self, id: ContactId) -> bool:
        return id in self.facts.identifiers or id in self.facts.named

    def contradicted(self, id: ContactId) -> bool:
        unrelated = (
            self.sender is not None
            and self.matched is None
            and not any(
                related(name_key(self.sender), name_key(own)) for own in self.names[id].names()
            )
        )
        return (
            any(other != id for other in self.proposers)
            or self.recipient == id
            or any(other != id for other in self.facts.identifiers)
            or unrelated
        )

    def green(self, id: ContactId) -> bool:
        if self.contradicted(id):
            return False
        backed = id in (self.classifier_names, self.neighbours_name)
        soft = self.matched == id and not self.ambiguous and backed
        return (self.hard(id) and id in self.proposers) or soft

    def sources(self, id: ContactId) -> tuple[CandidateSource, ...]:
        sources = []
        if self.matched == id:
            sources.append(
                CandidateSource(
                    signal=LLM,
                    names=self.model_names,
                    score=self.match_score,
                    threshold=self.thresholds.accept,
                    quote=self.quote,
                )
            )
        if id in self.probabilities:
            p = self.probabilities[id]
            named = p >= self.thresholds.classifier
            sources.append(
                CandidateSource(
                    signal=CLASSIFIER, names=named, score=p, threshold=self.thresholds.classifier
                )
            )
        if self.votes.get(id):
            sources.append(_neighbour_source(id, self.votes, self.neighbours, self.neighbours_name))
        if self.hard(id):
            sources.append(
                CandidateSource(
                    signal=FACTS,
                    identifiers=self.facts.identifiers.get(id, ()),
                    named_in_text=id in self.facts.named,
                )
            )
        return tuple(sources)


def judge_contact(
    signals: Signals,
    contacts: Sequence[Contact],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> FieldCheck:
    view = _contact_view(signals, contacts, thresholds)
    names = view.names
    candidates = [
        id
        for id in _unique(
            [
                *view.proposers,
                *([view.matched] if view.matched else []),
                *view.facts.identifiers,
                *(id for id, p in view.probabilities.items() if p >= 1 - thresholds.classifier),
                *view.votes,
            ]
        )
        if id in names
    ]
    ranked = sorted(
        candidates,
        key=lambda id: (
            -len(view.proposers.get(id, ())),
            -view.hard(id),
            -view.probabilities.get(id, 0.0),
            -(view.match_score if view.matched == id else 0.0),
            -view.votes.get(id, 0),
            names[id].name,
        ),
    )
    listed = [
        Candidate(
            value=str(id),
            label=names[id].name,
            sources=view.sources(id),
            contradicted=view.contradicted(id),
        )
        for id in ranked
    ]
    if view.sender and view.matched is None:
        source = CandidateSource(signal=LLM, quote=view.quote)
        listed.append(Candidate(value=None, label=view.sender, sources=(source,)))
    shown = _quote_names(view.quote, names[view.matched] if view.matched else None, view.sender)
    chosen = next((id for id in ranked if view.green(id)), None)
    if chosen is not None:
        score = max(
            view.match_score if view.matched == chosen else 0.0,
            view.probabilities.get(chosen, 0.0),
            thresholds.accept,
        )
        return FieldCheck(
            field=CONTACT,
            outcome=Outcome.OK,
            confidence=min(score, 1.0),
            proposed=view.sender,
            evidence=view.quote,
            value=str(chosen),
            quote_shows_value=shown,
            candidates=tuple(listed),
        )
    top = ranked[0] if ranked else None
    score = max(view.match_score, view.probabilities.get(top, 0.0) if top else 0.0)
    return FieldCheck(
        field=CONTACT,
        outcome=Outcome.UNCERTAIN,
        confidence=min(score, _UNSURE),
        reason=_contact_reason(view, top, signals.model is None),
        proposed=view.sender,
        evidence=view.quote,
        suggestion=None if top is None else str(top),
        new_name=view.sender if view.sender and view.matched is None else None,
        quote_shows_value=shown,
        candidates=tuple(listed),
    )


def _contact_view(
    signals: Signals, contacts: Sequence[Contact], thresholds: Thresholds
) -> _ContactView:
    pairs = [(contact, own) for contact in contacts for own in contact.names()]
    model = signals.model
    sender = model.sender if model else None
    matched: ContactId | None = None
    match_score, model_names, ambiguous = 0.0, False, False
    if sender:
        match = best_match(sender, pairs)
        if match.best is not None and match.score >= thresholds.suggest:
            matched, match_score = match.best.id, match.score
            ambiguous = match.runner_up >= match.score - thresholds.ambiguity
            model_names = match.score >= thresholds.accept and not ambiguous
    recipient: ContactId | None = None
    if model and model.recipient:
        found = best_match(model.recipient, pairs)
        if found.best is not None and found.score >= thresholds.accept:
            recipient = found.best.id
    names = {contact.id: contact for contact in contacts}
    # A model trained before a contact was deleted (two merged) still knows it: leave it out.
    ranking = [
        (id, p)
        for id, p in (signals.classifier.contacts if signals.classifier else ())
        if id in names
    ]
    classifier_names = _top(ranking, thresholds.classifier)
    neighbours = signals.neighbours or ()
    votes = Counter(item.contact for item in neighbours if item.contact in names)
    neighbours_name = _majority(votes, len(neighbours), undivided=True)
    proposers: dict[ContactId, tuple[str, ...]] = {}
    for signal, named in (
        (LLM, matched if model_names else None),
        (CLASSIFIER, classifier_names),
        (NEIGHBOURS, neighbours_name),
    ):
        if named is not None:
            proposers[named] = (*proposers.get(named, ()), signal)
    return _ContactView(
        names=names,
        sender=sender,
        quote=model.sender_quote if model else None,
        matched=matched,
        match_score=match_score,
        ambiguous=ambiguous,
        model_names=model_names,
        recipient=recipient,
        probabilities=dict(ranking),
        classifier_names=classifier_names,
        neighbours=neighbours,
        votes=votes,
        neighbours_name=neighbours_name,
        facts=signals.facts or FactsView(),
        proposers=proposers,
        thresholds=thresholds,
    )


def _contact_reason(view: _ContactView, top: ContactId | None, no_model: bool) -> str:
    names = view.names
    if len(view.proposers) > 1:
        return "the signals disagree: " + " or ".join(
            f"'{names[id].name}'" for id in view.proposers
        )
    if top is not None and view.recipient == top:
        return f"'{names[top].name}' is the recipient, not the sender"
    if len(view.facts.identifiers) > 1:
        return "identifiers of several contacts stand in the text"
    if top is None:
        if view.sender:
            return f"no contact matches '{view.sender}'; a new contact is proposed"
        silent = " (the language model did not answer)" if no_model else ""
        return f"no contact recognised{silent}"
    name = names[top].name
    if view.sender and view.matched is None:
        return f"'{view.sender}' is no contact, and only other signals point to '{name}'"
    if top not in view.proposers:
        if top == view.matched and view.match_score >= view.thresholds.accept:
            return f"'{view.sender}' matches several contacts"
        if top == view.matched:
            return f"'{view.sender}' is only similar to the contact '{name}'"
        return f"'{name}' is only suggested: no signal is sure of it"
    if not view.hard(top):
        return f"the contact '{name}' does not appear in the text"
    return f"only one signal names '{name}'"


def _quote_names(quote: str | None, contact: Contact | None, sender: str | None) -> bool | None:
    """Whether the model's quote shows the contact's name (or the sender as read)."""
    if not quote:
        return None
    key = name_key(quote)
    own = contact.names() if contact is not None else [sender] if sender else []
    return any(mentions(key, name_key(name)) for name in own)


# --- document type ----------------------------------------------------------------------------


def judge_type(
    signals: Signals,
    types: Sequence[DocumentType],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> FieldCheck:
    names = {kind.id: kind for kind in types}
    model = signals.model
    proposed = model.document_type if model else None
    by_model = _by_name(types, proposed) if proposed else None
    new = (model.new_document_type if model else None) or (
        proposed if proposed and by_model is None else None
    )
    classifier = signals.classifier
    ranking = [
        (id, p) for id, p in (classifier.document_types if classifier else ()) if id in names
    ]
    probabilities = dict(ranking)
    classifier_names = _top(ranking, thresholds.classifier)
    neighbours = signals.neighbours or ()
    votes = Counter(item.document_type for item in neighbours if item.document_type in names)
    neighbours_name = _majority(votes, len(neighbours))
    namers: dict[DocumentTypeId, tuple[str, ...]] = {}
    for signal, named in (
        (LLM, by_model.id if by_model else None),
        (CLASSIFIER, classifier_names),
        (NEIGHBOURS, neighbours_name),
    ):
        if named is not None:
            namers[named] = (*namers.get(named, ()), signal)

    known = signals.neighbours_know

    def covered_by_others(id: DocumentTypeId) -> bool:
        by_classifier = classifier is not None and id in classifier.known_types
        by_neighbours = bool(neighbours) and (known is None or id in known.document_types)
        return by_classifier or by_neighbours

    def contradicted(id: DocumentTypeId) -> bool:
        return any(other != id for other in namers)

    def green(id: DocumentTypeId) -> bool:
        naming = namers.get(id, ())
        sole = naming == (LLM,) and not covered_by_others(id)
        return not contradicted(id) and (len(naming) >= 2 or sole)

    candidates = [
        id
        for id in _unique(
            [
                *namers,
                *(id for id, p in probabilities.items() if p >= 1 - thresholds.classifier),
                *votes,
            ]
        )
        if id in names
    ]
    ranked = sorted(
        candidates,
        key=lambda id: (
            -len(namers.get(id, ())),
            -probabilities.get(id, 0.0),
            -votes.get(id, 0),
            names[id].name,
        ),
    )

    def sources(id: DocumentTypeId) -> tuple[CandidateSource, ...]:
        found = []
        if by_model is not None and by_model.id == id:
            found.append(CandidateSource(signal=LLM, names=True))
        if id in probabilities:
            p = probabilities[id]
            found.append(
                CandidateSource(
                    signal=CLASSIFIER,
                    names=p >= thresholds.classifier,
                    score=p,
                    threshold=thresholds.classifier,
                )
            )
        if votes.get(id):
            found.append(_neighbour_source(id, votes, neighbours, neighbours_name))
        return tuple(found)

    listed = [
        Candidate(
            value=str(id), label=names[id].name, sources=sources(id), contradicted=contradicted(id)
        )
        for id in ranked
    ]
    if new:
        listed.append(Candidate(value=None, label=new, sources=(CandidateSource(signal=LLM),)))
    chosen = next((id for id in ranked if green(id)), None)
    if chosen is not None:
        sure = len(namers[chosen]) >= 2 or not probabilities
        return FieldCheck(
            field=DOCUMENT_TYPE,
            outcome=Outcome.OK,
            confidence=1.0 if sure else max(probabilities.get(chosen, 0.0), thresholds.accept),
            proposed=proposed,
            value=str(chosen),
            candidates=tuple(listed),
        )
    top = ranked[0] if ranked else None
    return FieldCheck(
        field=DOCUMENT_TYPE,
        outcome=Outcome.UNCERTAIN,
        confidence=min(probabilities.get(top, 0.0) if top else 0.0, _UNSURE),
        reason=_type_reason(names, namers, top, new),
        proposed=proposed,
        suggestion=None if top is None else str(top),
        new_name=new,
        candidates=tuple(listed),
    )


def _type_reason(
    names: Mapping[DocumentTypeId, DocumentType],
    namers: Mapping[DocumentTypeId, tuple[str, ...]],
    top: DocumentTypeId | None,
    new: str | None,
) -> str:
    if len(namers) > 1:
        return "the signals disagree: " + " or ".join(f"'{names[id].name}'" for id in namers)
    if top is not None and top in namers:
        return f"only one signal names '{names[top].name}'"
    if new:
        return f"no document type fits; a new type '{new}' is proposed"
    if top is not None:
        return f"'{names[top].name}' is only suggested: no signal is sure of it"
    return "no document type recognised"


# --- tags -------------------------------------------------------------------------------------


def judge_tags(
    signals: Signals,
    tags: Sequence[Tag],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> FieldCheck:
    """Always OK: tags set are those two signals name, or the model alone where no other
    signal could have, without the ancestors of others set (`TagTree.normalise`); those one
    signal names are a suggestion (their ids). `tags`: all tags; only the classification's
    targets (`Tag.is_target`) are named."""
    targets = [tag for tag in tags if tag.is_target]
    names = {tag.id: tag for tag in targets}
    model = signals.model
    proposed_names = model.tags if model else ()
    by_model = {found.id for name in proposed_names if (found := _by_name(targets, name))}
    classifier = signals.classifier
    probabilities = {
        id: p for id, p in (classifier.tags if classifier else {}).items() if id in names
    }
    neighbours = signals.neighbours or ()
    carried = Counter(tag for item in neighbours for tag in item.tags)

    def namers(id: TagId) -> tuple[str, ...]:
        found = []
        if id in by_model:
            found.append(LLM)
        if probabilities.get(id, 0.0) >= thresholds.classifier:
            found.append(CLASSIFIER)
        if carried.get(id, 0) >= 2 and carried[id] > len(neighbours) / 2:
            found.append(NEIGHBOURS)
        return tuple(found)

    known = signals.neighbours_know

    def covered_by_others(id: TagId) -> bool:
        by_neighbours = bool(neighbours) and (known is None or id in known.tags)
        return by_neighbours or id in probabilities

    applied: list[TagId] = []
    suggested: list[TagId] = []
    for id in _unique([*by_model, *probabilities, *carried]):
        naming = namers(id)
        if id not in names or not naming:
            continue
        if len(naming) >= 2 or (naming == (LLM,) and not covered_by_others(id)):
            applied.append(id)
        else:
            suggested.append(id)
    by_name = sorted([*applied, *suggested], key=lambda id: names[id].name)
    kept = TagTree.of(tags).normalise(applied)
    applied = [id for id in applied if id in kept]
    candidates = tuple(
        Candidate(
            value=str(id),
            label=names[id].name,
            sources=tuple(CandidateSource(signal=signal, names=True) for signal in namers(id)),
        )
        for id in by_name
    )
    suggestion: list[JsonValue] = [
        str(id) for id in sorted(suggested, key=lambda id: names[id].name.casefold())
    ]
    proposed: JsonValue = None
    if model is not None:
        proposed = {"tags": list(model.tags), "new_tags": list(model.new_tags)}
    return FieldCheck(
        field=TAGS,
        outcome=Outcome.OK,
        confidence=1.0,
        proposed=proposed,
        value=[str(id) for id in by_name if id in applied],
        suggestion=suggestion or None,
        candidates=candidates,
    )


# --- topic ------------------------------------------------------------------------------------


def judge_topic(
    signals: Signals,
    contact: FieldCheck,
    contacts: Sequence[Contact],
    tags: Sequence[Tag],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> FieldCheck:
    """Always OK, as tags: the topic of the contact the judge decided (`contact`, the check of
    `judge_contact`), among the topics it enables that the classification sets
    (`Tag.is_topic_target`). Set if the contact is green and an identifier of the pair stands in
    the text and a signal names the topic, or two signals name it; otherwise a suggestion (its
    id), also for every topic under a contact that is only suggested. Signals or identifiers that
    name different topics leave it empty, with all candidates. `tags`: all tags."""
    green = contact.ok and isinstance(contact.value, str)
    chosen = contact.value if green else contact.suggestion
    owner = next((item for item in contacts if str(item.id) == chosen), None)
    model = signals.model
    proposed: JsonValue = None if model is None else model.topic
    if owner is None:
        return FieldCheck(
            field=TOPIC,
            outcome=Outcome.OK,
            confidence=1.0,
            reason="no contact: a topic is one of a contact's",
            proposed=proposed,
        )
    names = {tag.id: tag for tag in tags if tag.is_topic_target and tag.id in owner.topic_ids}
    by_model = _by_name(list(names.values()), model.topic) if model and model.topic else None
    classifier = signals.classifier
    ranking = [(id, p) for id, p in (classifier.topics if classifier else ()) if id in names]
    probabilities = dict(ranking)
    neighbours = signals.neighbours or ()
    votes = Counter(item.topic for item in neighbours if item.topic in names)
    evidence = {
        id: kinds
        for id, kinds in (signals.facts or FactsView()).topics.get(owner.id, {}).items()
        if id in names
    }
    namers: dict[TagId, tuple[str, ...]] = {}
    for signal, topic in (
        (LLM, by_model.id if by_model else None),
        (CLASSIFIER, _top(ranking, thresholds.classifier)),
        (NEIGHBOURS, _majority(votes, len(neighbours))),
    ):
        if topic is not None:
            namers[topic] = (*namers.get(topic, ()), signal)
    named = set(namers) | set(evidence)

    def sure(id: TagId) -> bool:
        naming = namers.get(id, ())
        return len(named) == 1 and ((id in evidence and bool(naming)) or len(naming) >= 2)

    candidates = [
        id
        for id in _unique(
            [
                *namers,
                *evidence,
                *(id for id, p in probabilities.items() if p >= 1 - thresholds.classifier),
                *votes,
            ]
        )
        if id in names
    ]
    ranked = sorted(
        candidates,
        key=lambda id: (
            -len(namers.get(id, ())),
            -(id in evidence),
            -probabilities.get(id, 0.0),
            -votes.get(id, 0),
            names[id].name.casefold(),
        ),
    )

    def sources(id: TagId) -> tuple[CandidateSource, ...]:
        found = []
        if by_model is not None and by_model.id == id:
            found.append(CandidateSource(signal=LLM, names=True))
        if id in probabilities:
            p = probabilities[id]
            found.append(
                CandidateSource(
                    signal=CLASSIFIER,
                    names=p >= thresholds.classifier,
                    score=p,
                    threshold=thresholds.classifier,
                )
            )
        if votes.get(id):
            found.append(
                _neighbour_source(id, votes, neighbours, _majority(votes, len(neighbours)))
            )
        if id in evidence:
            found.append(CandidateSource(signal=FACTS, identifiers=evidence[id]))
        return tuple(found)

    listed = tuple(
        Candidate(
            value=str(id),
            label=names[id].name,
            sources=sources(id),
            contradicted=any(other != id for other in named),
        )
        for id in ranked
    )
    top = ranked[0] if ranked else None
    if green and top is not None and sure(top):
        return FieldCheck(
            field=TOPIC,
            outcome=Outcome.OK,
            confidence=1.0,
            proposed=proposed,
            value=str(top),
            candidates=listed,
        )
    if len(named) > 1:
        reason = "the signals disagree: " + " or ".join(
            f"'{names[id].name}'" for id in ranked if id in named
        )
        suggestion = None
    elif top is None:
        reason = f"no topic of '{owner.name}' recognised"
        suggestion = None
    else:
        suggestion = str(top)
        if not green:
            reason = f"the contact '{owner.name}' is only suggested"
        elif top in namers:
            reason = f"only one signal names '{names[top].name}'"
        elif top in evidence:
            reason = f"only an identifier points to '{names[top].name}'"
        else:
            reason = f"'{names[top].name}' is only suggested: no signal is sure of it"
    return FieldCheck(
        field=TOPIC,
        outcome=Outcome.OK,
        confidence=1.0,
        reason=reason,
        proposed=proposed,
        suggestion=suggestion,
        candidates=listed,
    )


# --- helpers ----------------------------------------------------------------------------------


def _top[K](ranking: Sequence[tuple[K, float]], threshold: float) -> K | None:
    return ranking[0][0] if ranking and ranking[0][1] >= threshold else None


def _majority[K](votes: Counter[K], found: int, *, undivided: bool = False) -> K | None:
    """The value at least two and more than half of the `found` carry; `undivided`: and no
    other value two of them carry."""
    if not votes:
        return None
    (value, count), *others = votes.most_common()
    if undivided and any(other >= 2 for _, other in others):
        return None
    return value if count >= 2 and count > found / 2 else None


def _neighbour_source[K](
    id: K, votes: Counter[K], neighbours: Sequence[Neighbour], named: K | None
) -> CandidateSource:
    carrying = [item for item in neighbours if id in (item.contact, item.document_type, item.topic)]
    similarities = [item.similarity for item in carrying if item.similarity is not None]
    count = votes.get(id, 0)
    return CandidateSource(
        signal=NEIGHBOURS,
        names=named == id,
        score=count / len(neighbours) if neighbours else None,
        similarity=max(similarities) if similarities else None,
        votes=count,
        of=len(neighbours),
        documents=tuple(item.document for item in carrying),
    )


def _unique[K](items: Iterable[K]) -> list[K]:
    return list(dict.fromkeys(items))


def _by_name[T: (DocumentType, Tag)](items: Sequence[T], name: str) -> T | None:
    key = name.casefold()
    return next((item for item in items if item.name.casefold() == key), None)

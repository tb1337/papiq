"""Comparing names of organisations and people: the contact match of a classification.

Names are compared by a key: normalised (see `evidence.normalise`), umlauts as `ae`/`oe`/`ue`,
`&` as `und`, punctuation removed and legal forms (`GmbH`, `AG`, `e. V.`, ...) dropped. Equal
keys match exactly; otherwise the similarity is the best of difflib's ratio on the keys and on
their words sorted (word order does not matter).
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from papiq.core.domain.evidence import fold_umlauts, normalise

# Legal forms written with dots or spaces, removed before punctuation goes.
_DOTTED_FORMS = re.compile(
    r"\b(?:e\.\s?v|e\.\s?k|e\.\s?g|g\.\s?b\.\s?r|co\.\s?kg|mbh\s?&\s?co|"
    r"gmbh\s?&\s?co\.?\s?kg|haftungsbeschränkt|haftungsbeschraenkt)\b\.?"
)
_LEGAL_FORMS = frozenset(
    {
        "ag", "eg", "ek", "ev", "gbr", "gmbh", "ggmbh", "kg", "kgaa", "mbh", "ohg", "partg",
        "se", "ug", "co", "inc", "ltd", "llc", "plc", "corp", "sa", "sarl", "bv", "nv",
    }
)  # fmt: skip
_PUNCTUATION = re.compile(r"[^\w\s]|_")


def name_key(name: str) -> str:
    """The comparable form of a name; empty if nothing but legal forms and punctuation."""
    text = _DOTTED_FORMS.sub(" ", normalise(name)).replace("&", " und ")
    text = fold_umlauts(_PUNCTUATION.sub(" ", text))
    return " ".join(word for word in text.split() if word not in _LEGAL_FORMS)


def similarity(a: str, b: str) -> float:
    """0..1 for two name keys; 1 only if they are equal."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    plain = SequenceMatcher(None, a, b, autojunk=False).ratio()
    ordered = SequenceMatcher(
        None, " ".join(sorted(a.split())), " ".join(sorted(b.split())), autojunk=False
    ).ratio()
    return min(max(plain, ordered), 0.99)


def mentions(text_key: str, key: str) -> bool:
    """Whether the name key appears as whole words in a text turned into a key."""
    return bool(key) and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text_key) is not None


@dataclass(frozen=True, kw_only=True)
class NameMatch[T]:
    """The best candidate for a name, its similarity and the similarity of the runner-up."""

    best: T | None
    score: float
    runner_up: float


def best_match[T](name: str, candidates: Iterable[tuple[T, str]]) -> NameMatch[T]:
    """The candidate whose name (second item) is most similar to `name`. A candidate may come
    with several names (its aliases): its best one counts, and the runner-up is always another
    candidate (the same object)."""
    key = name_key(name)
    best: dict[int, tuple[float, int, T]] = {}
    for index, (item, other) in enumerate(candidates):
        score = similarity(key, name_key(other))
        known = best.get(id(item))
        if known is None or score > known[0]:
            best[id(item)] = (score, known[1] if known else index, item)
    scored = sorted(best.values(), key=lambda entry: (-entry[0], entry[1]))
    if not scored or scored[0][0] == 0:
        return NameMatch(best=None, score=0.0, runner_up=0.0)
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    return NameMatch(best=scored[0][2], score=scored[0][0], runner_up=runner_up)


_SHORTEST_WORD = 3  # letters of a word that counts for `named_share`


def named_share(text_key: str, text_words: Collection[str], key: str) -> float:
    """How much of a name key the text shows: 1 if it appears as a whole, otherwise the share
    of its words (of at least three letters) among `text_words` (the words of `text_key`).
    Cheap enough to rank all contacts for a prompt."""
    if mentions(text_key, key):
        return 1.0
    words = [word for word in key.split() if len(word) >= _SHORTEST_WORD]
    if not words:
        return 0.0
    found = sum(1 for word in words if word in text_words)
    return min(found / len(words), 0.99)

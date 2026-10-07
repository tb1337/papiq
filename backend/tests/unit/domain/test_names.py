import pytest

from papiq.core.domain.names import best_match, mentions, name_key, similarity


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Stadtwerke Musterstadt GmbH", "stadtwerke musterstadt"),
        ("Müller & Söhne GmbH & Co. KG", "mueller und soehne"),
        ("Sportverein Musterstadt e. V.", "sportverein musterstadt"),
        ("Dr. med. Anna Beispiel", "dr med anna beispiel"),
        ("ACME Inc.", "acme"),
        ("Bäckerei Schmidt (haftungsbeschränkt)", "baeckerei schmidt"),
        ("GmbH", ""),
    ],
)
def test_name_key(name: str, key: str) -> None:
    assert name_key(name) == key


def test_similarity() -> None:
    assert similarity("stadtwerke musterstadt", "stadtwerke musterstadt") == 1
    assert similarity("musterstadt stadtwerke", "stadtwerke musterstadt") == 0.99
    assert 0.9 < similarity("stadtwerke musterstad", "stadtwerke musterstadt") < 1
    assert similarity("aok", "allianz") < 0.5
    assert similarity("", "x") == 0


def test_best_match() -> None:
    candidates = [(1, "Stadtwerke Musterstadt GmbH"), (2, "Stadtwerke Beispielstadt"), (3, "AOK")]
    match = best_match("Stadtwerke Musterstadt", candidates)
    assert (match.best, match.score) == (1, 1.0)
    assert match.runner_up < 0.9

    tie = best_match("Muster", [(1, "Muster GmbH"), (2, "Muster AG")])
    assert (tie.best, tie.score, tie.runner_up) == (1, 1.0, 1.0)

    assert best_match("Anything", []).best is None
    assert best_match("GmbH", candidates).best is None


def test_mentions_whole_words() -> None:
    text = name_key("Ihre Stadtwerke Musterstadt GmbH, Postfach 12")
    assert mentions(text, "stadtwerke musterstadt")
    assert not mentions(text, "werke muster")
    assert not mentions(text, "")

"""Contract suite for the `PatternMatcher` port: regular expressions of rules, with a time
limit."""

import asyncio
import time

import pytest

from papiq.core.domain.errors import PatternTimeoutError, ValidationError
from papiq.core.ports import PatternMatcher

# Catastrophic backtracking: exponential in the number of `a`s, since the `b` never matches.
CATASTROPHIC = r"(a|aa)+$"
NO_MATCH_SLOWLY = "a" * 40 + "b"


class PatternMatcherContract:
    """Needs the fixture `pattern_matcher`, with a time limit of at most a second."""

    async def test_finds_the_pattern_anywhere(self, pattern_matcher: PatternMatcher) -> None:
        text = "Stadtwerke München\nStromrechnung Nr. 4711\nBetrag: 84,20 EUR"
        assert await pattern_matcher.search(r"Rechnung", text, case_sensitive=False)
        assert await pattern_matcher.search(r"Nr\. \d{4}", text, case_sensitive=True)
        assert await pattern_matcher.search(r"^Stromrechnung", text, case_sensitive=True) is False
        assert await pattern_matcher.search(r"(?m)^Stromrechnung", text, case_sensitive=True)
        assert await pattern_matcher.search(r"München", text, case_sensitive=True)

    async def test_no_match(self, pattern_matcher: PatternMatcher) -> None:
        assert await pattern_matcher.search("Mahnung", "Rechnung", case_sensitive=False) is False
        assert await pattern_matcher.search(r"\d", "", case_sensitive=False) is False

    async def test_case_sensitivity(self, pattern_matcher: PatternMatcher) -> None:
        assert await pattern_matcher.search("rechnung", "STROMRECHNUNG", case_sensitive=False)
        assert not await pattern_matcher.search("rechnung", "STROMRECHNUNG", case_sensitive=True)
        assert await pattern_matcher.search("ÄRZTE", "ärzte", case_sensitive=False)
        assert not await pattern_matcher.search("ÄRZTE", "ärzte", case_sensitive=True)
        # The same pattern with both settings: one does not stand in for the other.
        assert await pattern_matcher.search("Abc", "abc", case_sensitive=False)
        assert not await pattern_matcher.search("Abc", "abc", case_sensitive=True)

    @pytest.mark.parametrize("pattern", ["(", "[a-", "a{2,1}", "(?<name"])
    async def test_an_invalid_pattern_is_a_validation_error(
        self, pattern_matcher: PatternMatcher, pattern: str
    ) -> None:
        for case_sensitive in (False, True):
            with pytest.raises(ValidationError):
                await pattern_matcher.search(pattern, "text", case_sensitive=case_sensitive)

    @pytest.mark.parametrize("case_sensitive", [False, True])
    async def test_catastrophic_backtracking_is_stopped(
        self, pattern_matcher: PatternMatcher, case_sensitive: bool
    ) -> None:
        started = time.monotonic()
        with pytest.raises(PatternTimeoutError):
            await pattern_matcher.search(
                CATASTROPHIC, NO_MATCH_SLOWLY, case_sensitive=case_sensitive
            )
        assert time.monotonic() - started < 5

    async def test_a_slow_search_does_not_block_the_event_loop(
        self, pattern_matcher: PatternMatcher
    ) -> None:
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        try:
            with pytest.raises(PatternTimeoutError):
                await pattern_matcher.search(CATASTROPHIC, NO_MATCH_SLOWLY, case_sensitive=False)
        finally:
            ticker.cancel()
        assert ticks >= 1

    async def test_works_again_after_a_timeout(self, pattern_matcher: PatternMatcher) -> None:
        with pytest.raises(PatternTimeoutError):
            await pattern_matcher.search(CATASTROPHIC, NO_MATCH_SLOWLY, case_sensitive=False)
        assert await pattern_matcher.search(CATASTROPHIC, "aaaa", case_sensitive=False)

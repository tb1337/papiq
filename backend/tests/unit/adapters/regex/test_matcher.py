"""The `regex` pattern matcher passes the contract; its patterns behave like Python's `re`
(with which the domain checks them)."""

import re

import pytest

from papiq.adapters.outbound.regex import RegexPatternMatcher
from papiq.adapters.outbound.regex.matcher import _compile
from papiq.core.domain.errors import ValidationError
from tests.contracts.patterns import PatternMatcherContract

TIMEOUT = 0.05  # seconds


@pytest.fixture
def pattern_matcher() -> RegexPatternMatcher:
    return RegexPatternMatcher(TIMEOUT)


class TestRegexPatternMatcher(PatternMatcherContract):
    pass


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_the_time_limit_must_be_positive(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        RegexPatternMatcher(timeout)


async def test_patterns_follow_the_syntax_of_re(pattern_matcher: RegexPatternMatcher) -> None:
    """VERSION0: `--` in a set is a range as in `re`, not a set difference (VERSION1)."""
    pattern, text = "^[a-z--x]$", "x"
    with pytest.warns(FutureWarning):  # `re` warns of a possible set difference
        expected = re.search(pattern, text) is not None
    assert expected
    assert await pattern_matcher.search(pattern, text, case_sensitive=True) is expected


async def test_an_invalid_pattern_names_the_pattern(pattern_matcher: RegexPatternMatcher) -> None:
    with pytest.raises(ValidationError, match=r"invalid pattern '\('"):
        await pattern_matcher.search("(", "text", case_sensitive=False)


async def test_compiled_patterns_are_cached_per_case_setting(
    pattern_matcher: RegexPatternMatcher,
) -> None:
    pattern = "papiq-cache-test-[0-9]+"
    before = _compile.cache_info()
    for _ in range(3):
        assert await pattern_matcher.search(pattern, "papiq-cache-test-42", case_sensitive=True)
        assert await pattern_matcher.search(pattern, "PAPIQ-CACHE-TEST-42", case_sensitive=False)
    after = _compile.cache_info()
    assert after.misses - before.misses == 2  # once per case setting
    assert after.hits - before.hits == 4
    assert _compile(pattern, True) is _compile(pattern, True)
    assert _compile(pattern, True) is not _compile(pattern, False)

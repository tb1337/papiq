"""Searches run in a worker thread with the GIL released (`concurrent=True`), so a slow pattern
blocks neither the event loop nor other threads until its time limit stops it."""

import asyncio
from functools import lru_cache

import regex

from papiq.core.domain.errors import PatternTimeoutError, ValidationError


@lru_cache(maxsize=512)
def _compile(pattern: str, case_sensitive: bool) -> "regex.Pattern[str]":
    flags = regex.VERSION0 if case_sensitive else regex.VERSION0 | regex.IGNORECASE
    try:
        return regex.compile(pattern, flags)
    except regex.error as error:
        raise ValidationError(f"invalid pattern {pattern!r}: {error}") from None


class RegexPatternMatcher:
    def __init__(self, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._timeout = timeout

    async def search(self, pattern: str, text: str, *, case_sensitive: bool) -> bool:
        compiled = _compile(pattern, case_sensitive)
        return await asyncio.to_thread(self._search, compiled, text)

    def _search(self, compiled: "regex.Pattern[str]", text: str) -> bool:
        try:
            return compiled.search(text, concurrent=True, timeout=self._timeout) is not None
        except TimeoutError:
            raise PatternTimeoutError(
                f"pattern {compiled.pattern!r} took longer than {self._timeout} s"
            ) from None

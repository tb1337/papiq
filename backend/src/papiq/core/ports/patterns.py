"""Regular expressions of rules, with a time limit.

Patterns come from users and run against whole document texts; a pattern with catastrophic
backtracking must not block the worker or the API. First adapter: the `regex` package, which
can stop a search after a time limit.
"""

from typing import Protocol


class PatternMatcher(Protocol):
    async def search(self, pattern: str, text: str, *, case_sensitive: bool) -> bool:
        """Whether `pattern` occurs anywhere in `text`. PatternTimeoutError if the search takes
        longer than the configured limit; ValidationError if the pattern is invalid."""
        ...

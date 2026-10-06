from typing import Protocol


class SearchIndex(Protocol):
    """Derived full-text and vector index; can always be rebuilt from repository and store.

    Every query is filtered to the drawers the caller may see. First adapter: Meilisearch.
    """

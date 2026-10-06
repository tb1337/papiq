from typing import Protocol


class ObjectStore(Protocol):
    """Binary storage for immutable originals (keyed by SHA-256) and derivatives.

    First adapters: S3, local filesystem for development and tests.
    """

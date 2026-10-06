from typing import Protocol


class ObjectStore(Protocol):
    """Binary storage for immutable originals (keyed by SHA-256) and derivatives.

    Adapters: S3 (first server: Garage) and local filesystem, equal options chosen by
    configuration. The core never relies on storage-side events; events come from the outbox.
    """

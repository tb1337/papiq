from typing import Protocol


class ObjectStore(Protocol):
    """Binary storage for immutable originals (keyed by SHA-256) and derivatives.

    Adapters: S3 (first server: Garage) and local filesystem, equal options chosen by
    configuration. The core never relies on storage-side events; events come from the outbox.
    Keys are relative paths such as `originals/<sha256>`.
    """

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store `data` under `key`, replacing an existing object."""
        ...

    async def get(self, key: str) -> bytes:
        """NotFoundError if there is no object under `key`."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove the object; does nothing if it does not exist."""
        ...

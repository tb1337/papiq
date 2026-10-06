import re
from pathlib import Path
from typing import Protocol

# One path segment of an object key: no empty, hidden (`.x`) or relative (`.`, `..`) segments.
_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


class ObjectStore(Protocol):
    """Binary storage for immutable originals (keyed by SHA-256) and derivatives.

    Adapters: S3 (first server: Garage) and local filesystem, equal options chosen by
    configuration. The core never relies on storage-side events; events come from the outbox.

    Keys are relative paths such as `originals/<sha256>`: segments of letters, digits, `.`, `_`
    and `-`, separated by `/`, none empty or starting with `.`. Other keys raise ValueError
    (see `check_key`).

    `put` and `get` hold the whole object in memory and suit small objects. Files of any size
    go through `upload` and `download`, which work on local files in chunks.
    """

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store `data` under `key`, replacing an existing object."""
        ...

    async def get(self, key: str) -> bytes:
        """NotFoundError if there is no object under `key`."""
        ...

    async def upload(self, key: str, source: Path, *, content_type: str) -> None:
        """Store the content of the local file `source` under `key`, replacing an existing
        object. Readers see the old or the new object, never a partial one."""
        ...

    async def download(self, key: str, target: Path) -> None:
        """Write the object to the local file `target`, replacing it if it exists. NotFoundError
        if there is no object under `key`; `target` is then left as it was."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove the object; does nothing if it does not exist."""
        ...


def check_key(key: str) -> str:
    """Return `key` if it is a valid object key, otherwise raise ValueError."""
    if not isinstance(key, str) or not all(_SEGMENT.fullmatch(part) for part in key.split("/")):
        raise ValueError(f"invalid object key {key!r}")
    return key

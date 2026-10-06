import asyncio
from pathlib import Path

from papiq.core.domain.errors import NotFoundError
from papiq.core.ports.object_store import check_key


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._objects[check_key(key)] = (bytes(data), content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[check_key(key)][0]
        except KeyError:
            raise NotFoundError("object", key) from None

    async def upload(self, key: str, source: Path, *, content_type: str) -> None:
        check_key(key)
        data = await asyncio.to_thread(source.read_bytes)
        self._objects[key] = (data, content_type)

    async def download(self, key: str, target: Path) -> None:
        data = await self.get(key)
        await asyncio.to_thread(target.write_bytes, data)

    async def exists(self, key: str) -> bool:
        return check_key(key) in self._objects

    async def delete(self, key: str) -> None:
        self._objects.pop(check_key(key), None)

    def content_type(self, key: str) -> str:
        """The content type an object was stored with (for tests)."""
        try:
            return self._objects[check_key(key)][1]
        except KeyError:
            raise NotFoundError("object", key) from None

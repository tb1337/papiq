from papiq.core.domain.errors import NotFoundError


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._objects[_check(key)] = (bytes(data), content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[_check(key)][0]
        except KeyError:
            raise NotFoundError("object", key) from None

    async def exists(self, key: str) -> bool:
        return _check(key) in self._objects

    async def delete(self, key: str) -> None:
        self._objects.pop(_check(key), None)


def _check(key: str) -> str:
    if not key or key.startswith("/"):
        raise ValueError(f"invalid object key {key!r}")
    return key

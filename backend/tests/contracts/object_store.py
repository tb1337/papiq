import pytest

from papiq.core.domain.errors import NotFoundError
from papiq.core.ports import ObjectStore

KEY = "originals/0123abcd"


class ObjectStoreContract:
    async def test_put_and_get(self, object_store: ObjectStore) -> None:
        data = bytes(range(256)) * 10
        await object_store.put(KEY, data, content_type="application/pdf")
        assert await object_store.get(KEY) == data
        assert await object_store.exists(KEY)

    async def test_empty_object(self, object_store: ObjectStore) -> None:
        await object_store.put(KEY, b"", content_type="text/plain")
        assert await object_store.get(KEY) == b""

    async def test_put_replaces(self, object_store: ObjectStore) -> None:
        await object_store.put(KEY, b"old", content_type="text/plain")
        await object_store.put(KEY, b"new", content_type="text/plain")
        assert await object_store.get(KEY) == b"new"

    async def test_keys_are_independent(self, object_store: ObjectStore) -> None:
        await object_store.put("archive/a.pdf", b"a", content_type="application/pdf")
        await object_store.put("archive/b.pdf", b"b", content_type="application/pdf")
        assert await object_store.get("archive/a.pdf") == b"a"
        assert await object_store.get("archive/b.pdf") == b"b"

    async def test_missing_object(self, object_store: ObjectStore) -> None:
        assert not await object_store.exists(KEY)
        with pytest.raises(NotFoundError):
            await object_store.get(KEY)

    async def test_delete_is_idempotent(self, object_store: ObjectStore) -> None:
        await object_store.put(KEY, b"x", content_type="text/plain")
        await object_store.delete(KEY)
        await object_store.delete(KEY)
        assert not await object_store.exists(KEY)

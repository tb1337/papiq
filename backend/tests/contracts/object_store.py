import os
from pathlib import Path

import pytest

from papiq.core.domain.errors import NotFoundError
from papiq.core.ports import ObjectStore

KEY = "originals/0123abcd"

# Larger than the S3 multipart threshold (8 MiB), so S3 adapters upload in parts.
LARGE = 9 * 1024 * 1024 + 123

INVALID_KEYS = ["", "/abs", "a//b", "a/", "a/../b", "..", "a/./b", ".hidden", "a\\b", "a b", "ä"]


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

    async def test_a_key_can_be_a_prefix_of_another_one(self, object_store: ObjectStore) -> None:
        await object_store.put("documents/a.pdf", b"a", content_type="application/pdf")
        await object_store.put("documents/a.pdf.md", b"md", content_type="text/markdown")
        assert await object_store.get("documents/a.pdf") == b"a"

    @pytest.mark.parametrize("key", INVALID_KEYS)
    async def test_invalid_keys_are_rejected(
        self, object_store: ObjectStore, key: str, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        source.write_bytes(b"x")
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.put(key, b"x", content_type="text/plain")
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.get(key)
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.upload(key, source, content_type="text/plain")
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.download(key, tmp_path / "target")
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.exists(key)
        with pytest.raises(ValueError, match="invalid object key"):
            await object_store.delete(key)

    # --- files ----------------------------------------------------------------------------------

    async def test_upload_and_download_a_large_file(
        self, object_store: ObjectStore, tmp_path: Path
    ) -> None:
        data = os.urandom(LARGE)
        source = tmp_path / "source.pdf"
        source.write_bytes(data)
        await object_store.upload(KEY, source, content_type="application/pdf")
        assert source.read_bytes() == data  # the source is left alone

        target = tmp_path / "target.pdf"
        await object_store.download(KEY, target)
        assert target.read_bytes() == data
        assert await object_store.get(KEY) == data

    async def test_upload_an_empty_file(self, object_store: ObjectStore, tmp_path: Path) -> None:
        source = tmp_path / "empty"
        source.write_bytes(b"")
        await object_store.upload(KEY, source, content_type="text/plain")
        target = tmp_path / "target"
        await object_store.download(KEY, target)
        assert target.read_bytes() == b""

    async def test_upload_replaces(self, object_store: ObjectStore, tmp_path: Path) -> None:
        await object_store.put(KEY, b"old", content_type="text/plain")
        source = tmp_path / "source"
        source.write_bytes(b"new")
        await object_store.upload(KEY, source, content_type="text/plain")
        assert await object_store.get(KEY) == b"new"

    async def test_download_replaces_the_target(
        self, object_store: ObjectStore, tmp_path: Path
    ) -> None:
        await object_store.put(KEY, b"short", content_type="text/plain")
        target = tmp_path / "target"
        target.write_bytes(b"a much longer existing file")
        await object_store.download(KEY, target)
        assert target.read_bytes() == b"short"

    async def test_download_of_a_missing_object(
        self, object_store: ObjectStore, tmp_path: Path
    ) -> None:
        missing = tmp_path / "missing"
        with pytest.raises(NotFoundError):
            await object_store.download(KEY, missing)
        assert not missing.exists()

        existing = tmp_path / "existing"
        existing.write_bytes(b"keep")
        with pytest.raises(NotFoundError):
            await object_store.download(KEY, existing)
        assert existing.read_bytes() == b"keep"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["existing"]

    async def test_upload_of_a_missing_file(
        self, object_store: ObjectStore, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            await object_store.upload(KEY, tmp_path / "missing", content_type="text/plain")
        assert not await object_store.exists(KEY)

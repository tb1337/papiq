"""The filesystem object store passes the contract and writes atomically."""

from pathlib import Path

import pytest

from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.core.ports import ObjectStore
from tests.contracts.object_store import ObjectStoreContract


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "objects"


@pytest.fixture
def object_store(root: Path) -> ObjectStore:
    return FilesystemObjectStore(root)


class TestFilesystemObjectStore(ObjectStoreContract):
    pass


async def test_objects_are_files_below_the_root(root: Path) -> None:
    store = FilesystemObjectStore(root)
    await store.put("documents/abc/archive.pdf", b"pdf", content_type="application/pdf")
    assert (root / "documents" / "abc" / "archive.pdf").read_bytes() == b"pdf"
    assert [path.name for path in (root / "documents" / "abc").iterdir()] == ["archive.pdf"]


async def test_a_failed_write_keeps_the_old_object(root: Path, tmp_path: Path) -> None:
    store = FilesystemObjectStore(root)
    await store.put("a", b"old", content_type="text/plain")
    source = tmp_path / "source"
    source.mkdir()  # reading a directory fails in the middle of the write
    with pytest.raises(IsADirectoryError):
        await store.upload("a", source, content_type="text/plain")
    assert await store.get("a") == b"old"
    assert [path.name for path in root.iterdir()] == ["a"]  # no temporary file left


async def test_a_directory_is_not_an_object(root: Path) -> None:
    store = FilesystemObjectStore(root)
    await store.put("a/b", b"x", content_type="text/plain")
    assert not await store.exists("a")
    await store.delete("a")
    assert await store.get("a/b") == b"x"


async def test_check_needs_a_usable_root(tmp_path: Path) -> None:
    await FilesystemObjectStore(tmp_path / "new" / "objects").check()  # created if missing
    blocked = tmp_path / "file"
    blocked.write_bytes(b"")
    with pytest.raises(OSError):
        await FilesystemObjectStore(blocked).check()

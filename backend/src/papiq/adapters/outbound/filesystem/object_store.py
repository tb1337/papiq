"""Object store in a local directory: one file per object, the key is the relative path.

Writes are atomic: the content goes to a temporary file next to the target, is flushed to disk,
then renamed over the target. Readers see the old or the new file, never a partial one; after a
crash only a temporary file may be left. Temporary names start with a dot, which no key segment
may, so they never collide with objects.

The content type is not stored; the port does not return it. File operations run in threads.
Unlike S3, a key cannot also be the directory of another key (`a` and `a/b`); Papiq's key
scheme never needs that.
"""

import asyncio
import contextlib
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from papiq.core.domain.errors import NotFoundError
from papiq.core.ports.object_store import check_key

_CHUNK = 1024 * 1024


class FilesystemObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(_write_atomically, path, lambda out: out.write(data))

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            raise NotFoundError("object", key) from None

    async def upload(self, key: str, source: Path, *, content_type: str) -> None:
        path = self._path(key)

        def copy(out: BinaryIO) -> None:
            with source.open("rb") as file:
                shutil.copyfileobj(file, out, _CHUNK)

        # Open the source first: a missing source must not leave anything behind.
        await asyncio.to_thread(_check_readable, source)
        await asyncio.to_thread(_write_atomically, path, copy)

    async def download(self, key: str, target: Path) -> None:
        path = self._path(key)

        def copy() -> None:
            try:
                file = path.open("rb")
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
                raise NotFoundError("object", key) from None
            with file:
                _write_atomically(target, lambda out: shutil.copyfileobj(file, out, _CHUNK))

        await asyncio.to_thread(copy)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def remove() -> None:
            if path.is_file():
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()

        await asyncio.to_thread(remove)

    def _path(self, key: str) -> Path:
        # Valid keys have no empty, `.` or `..` segments, so the path stays below the root.
        return self._root.joinpath(*check_key(key).split("/"))


def _check_readable(source: Path) -> None:
    with source.open("rb"):
        pass


def _write_atomically(path: Path, write: Callable[[BinaryIO], object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as out:
            write(out)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable. Not every platform can open directories; skip it there."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

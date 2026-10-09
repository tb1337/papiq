"""Streaming multipart upload: the file part goes to a temporary file in chunks and is hashed
on the way, so an upload is never held in memory. The size limit is enforced while receiving:
early by `Content-Length`, otherwise as soon as the file part, or the whole body, grows beyond
it (the body may exceed the file limit by `_OVERHEAD` for framing and fields).

Expected form: one part `file` with a filename, and optional small text fields; only the
fields the caller names are kept, others are skipped.
"""

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from papiq.core.domain.documents import Sha256
from papiq.core.services.pipeline import IncomingFile

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

FILE_FIELD = "file"
_FIELD_LIMIT = 1024  # bytes of a text field
_OVERHEAD = 64 * 1024  # bytes of multipart framing and text fields beyond the file
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME = 255


class UploadTooLargeError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"the file is larger than {limit} bytes")
        self.limit = limit


class MalformedUploadError(Exception):
    """The request is not the expected multipart form."""


@dataclass
class Upload:
    file: IncomingFile
    filename: str
    fields: dict[str, str]


async def read_upload(
    request: Request,
    *,
    max_size: int,
    fields: Collection[str] = (),
    limits: Mapping[str, int] | None = None,
) -> Upload:
    """Read the multipart body of `request`, keeping the text fields named in `fields` (each up
    to 1 KiB, or its limit in `limits`, which must stay well below `_OVERHEAD`). The caller
    removes `upload.file.path` when done; on error nothing is left behind."""
    content_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise MalformedUploadError("expected multipart/form-data with a boundary")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > max_size + _OVERHEAD:
        raise UploadTooLargeError(max_size)

    descriptor, name = await asyncio.to_thread(tempfile.mkstemp, prefix="papiq-upload-")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            state = _State(target, max_size, frozenset(fields), dict(limits or {}))
            parser = MultipartParser(boundary, cast("MultipartCallbacks", state.callbacks()))
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > max_size + _OVERHEAD:
                    raise UploadTooLargeError(max_size)
                try:
                    parser.write(chunk)
                except MultipartParseError as error:
                    raise MalformedUploadError(f"invalid multipart body: {error}") from None
                state.check()
                await state.flush()
        if not state.finished:
            raise MalformedUploadError("the multipart body is incomplete")
        if state.filename is None:
            raise MalformedUploadError(f"the form has no file part '{FILE_FIELD}'")
        return Upload(
            file=IncomingFile(path=path, sha256=Sha256(state.digest.hexdigest()), size=state.size),
            filename=state.filename,
            fields=state.fields,
        )
    except BaseException:
        await asyncio.shield(asyncio.to_thread(path.unlink, missing_ok=True))
        raise


@dataclass
class _State:
    """Collects the parser's callbacks; the file data is written between parser calls."""

    target: BinaryIO
    max_size: int
    wanted: frozenset[str]
    limits: dict[str, int]
    digest: Any = field(default_factory=hashlib.sha256)
    size: int = 0
    filename: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    finished: bool = False
    _pending: list[bytes] = field(default_factory=list)
    _headers: dict[str, str] = field(default_factory=dict)
    _header_field: bytearray = field(default_factory=bytearray)
    _header_value: bytearray = field(default_factory=bytearray)
    _part: str | None = None  # "file", the name of a text field, or None (ignored part)
    _value: bytearray = field(default_factory=bytearray)
    _error: Exception | None = None

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field_data,
            "on_header_value": self._header_value_data,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
            "on_end": self._end,
        }

    def check(self) -> None:
        if self._error is not None:
            raise self._error

    async def flush(self) -> None:
        if self._pending:
            data, self._pending = b"".join(self._pending), []
            await asyncio.to_thread(self.target.write, data)

    def _part_begin(self) -> None:
        self._headers = {}
        self._part = None
        self._value = bytearray()

    def _header_field_data(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def _header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        name = self._header_field.decode("latin-1").lower()
        # As latin-1 the bytes survive `parse_options_header`, which encodes them as latin-1 again;
        # names and file names are decoded as UTF-8 afterwards (a file name may hold any letter).
        self._headers[name] = self._header_value.decode("latin-1")
        self._header_field, self._header_value = bytearray(), bytearray()

    def _headers_finished(self) -> None:
        disposition, options = parse_options_header(self._headers.get("content-disposition"))
        if disposition != b"form-data" or b"name" not in options:
            self._fail(MalformedUploadError("a part has no form-data name"))
            return
        name = options[b"name"].decode("utf-8", errors="replace")
        if name == FILE_FIELD:
            if self.filename is not None:
                self._fail(MalformedUploadError("only one file per upload"))
                return
            raw = options.get(b"filename", b"").decode("utf-8", errors="replace")
            self.filename = clean_filename(raw)
            self._part = FILE_FIELD
        elif b"filename" in options or name not in self.wanted:
            self._part = None  # other files and unknown fields are skipped
        else:
            self._part = name

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if self._part is None or self._error is not None:
            return
        chunk = data[start:end]
        if self._part == FILE_FIELD:
            self.size += len(chunk)
            if self.size > self.max_size:
                self._fail(UploadTooLargeError(self.max_size))
                return
            self.digest.update(chunk)
            self._pending.append(chunk)
        else:
            self._value += chunk
            if len(self._value) > self.limits.get(self._part, _FIELD_LIMIT):
                self._fail(MalformedUploadError(f"the field '{self._part}' is too long"))

    def _part_end(self) -> None:
        if self._part not in (None, FILE_FIELD):
            self.fields[self._part] = self._value.decode("utf-8", errors="replace")

    def _end(self) -> None:
        self.finished = True

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = error


def clean_filename(raw: str) -> str:
    """The last path segment, without control characters; `upload` if nothing is left."""
    name = PurePosixPath(raw.replace("\\", "/")).name
    name = _CONTROL.sub("", name).strip()
    name = name[:_MAX_FILENAME]
    return name if name not in ("", ".", "..") else "upload"

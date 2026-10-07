"""Object store on an S3-compatible server (first server: Garage), with aioboto3.

One client per store, opened on first use and closed with `aclose`. Files are uploaded and
downloaded by aioboto3's transfer functions, in parts above 8 MiB. S3 replaces objects
atomically, so readers never see a partial object; a download goes to a temporary file next to
the target that is renamed when complete.

Checksums are only sent and checked where S3 requires them: newer botocore versions add CRC
checksums to every request by default, which not every S3-compatible server supports.
"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import aioboto3
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from papiq.core.domain.errors import NotFoundError
from papiq.core.ports.object_store import check_key

_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


class S3ObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        path_style: bool = True,
        prefix: str = "",
    ) -> None:
        """`prefix` is put in front of every key, e.g. `tests/<id>/` to keep tests apart."""
        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        self._endpoint_url = endpoint_url
        self._config = AioConfig(
            s3={"addressing_style": "path" if path_style else "virtual"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"mode": "standard", "max_attempts": 3},
        )
        self._bucket = bucket
        self._prefix = prefix
        self._stack = AsyncExitStack()
        self._client: Any = None
        self._lock = asyncio.Lock()

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        s3 = await self._s3()
        await s3.put_object(
            Bucket=self._bucket, Key=self._key(key), Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        s3 = await self._s3()
        try:
            response = await s3.get_object(Bucket=self._bucket, Key=self._key(key))
        except ClientError as error:
            raise _translate(error, key) from None
        async with response["Body"] as body:
            data: bytes = await body.read()
        return data

    async def upload(self, key: str, source: Path, *, content_type: str) -> None:
        name = self._key(key)
        s3 = await self._s3()
        # Fail on a missing source before anything is sent.
        await asyncio.to_thread(_check_readable, source)
        await s3.upload_file(
            str(source), self._bucket, name, ExtraArgs={"ContentType": content_type}
        )

    async def download(self, key: str, target: Path) -> None:
        name = self._key(key)
        s3 = await self._s3()
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            await s3.download_file(self._bucket, name, str(temporary))
            await asyncio.to_thread(temporary.replace, target)
        except ClientError as error:
            raise _translate(error, key) from None
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        s3 = await self._s3()
        try:
            await s3.head_object(Bucket=self._bucket, Key=self._key(key))
        except ClientError as error:
            if _code(error) in _NOT_FOUND:
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        s3 = await self._s3()
        await s3.delete_object(Bucket=self._bucket, Key=self._key(key))

    async def check(self) -> None:
        """For health checks: raises unless the bucket exists and the credentials work."""
        s3 = await self._s3()
        await s3.head_bucket(Bucket=self._bucket)

    async def aclose(self) -> None:
        """Close the client; a later call opens a new one."""
        await self._stack.aclose()
        self._client = None

    async def _s3(self) -> Any:
        async with self._lock:
            if self._client is None:
                self._client = await self._stack.enter_async_context(
                    self._session.client("s3", endpoint_url=self._endpoint_url, config=self._config)
                )
            return self._client

    def _key(self, key: str) -> str:
        return self._prefix + check_key(key)


def _check_readable(source: Path) -> None:
    with source.open("rb"):
        pass


def _code(error: Exception) -> str:
    response = getattr(error, "response", None) or {}
    return str(response.get("Error", {}).get("Code", ""))


def _translate(error: Exception, key: str) -> Exception:
    if _code(error) in _NOT_FOUND:
        return NotFoundError("object", key)
    return error

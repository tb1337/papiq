import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aioboto3
import pytest

from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.composition.settings import Settings, load_settings
from tests import probes
from tests.builders import SECRET_KEY


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The configuration from the environment: in the devcontainer, the compose services. The
    secret key the API requires is not what these tests are about; a test key fills in."""
    os.environ.setdefault("PAPIQ_SECRET_KEY", SECRET_KEY)
    return load_settings()


@pytest.fixture(scope="session")
def s3_settings(settings: Settings) -> Settings:
    if settings.storage_type != "s3" or settings.s3_endpoint_url is None:
        pytest.skip("PAPIQ_STORAGE_TYPE is not s3")
    if probes.http_status(str(settings.s3_endpoint_url)) is None:
        pytest.skip(f"S3 endpoint not reachable at {settings.s3_endpoint_url}")
    return settings


def s3_store(settings: Settings, prefix: str) -> S3ObjectStore:
    assert settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key
    return S3ObjectStore(
        endpoint_url=str(settings.s3_endpoint_url),
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        path_style=settings.s3_path_style,
        prefix=prefix,
    )


async def remove_prefix(settings: Settings, prefix: str) -> None:
    assert settings.s3_access_key_id and settings.s3_secret_access_key
    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
    )
    async with session.client("s3", endpoint_url=str(settings.s3_endpoint_url)) as s3:
        listing = await s3.list_objects_v2(Bucket=settings.s3_bucket, Prefix=prefix)
        for item in listing.get("Contents", []):
            await s3.delete_object(Bucket=settings.s3_bucket, Key=item["Key"])


@asynccontextmanager
async def s3_test_store(settings: Settings) -> AsyncIterator[S3ObjectStore]:
    """A store below a key prefix of its own, removed afterwards."""
    prefix = f"tests/{secrets.token_hex(8)}/"
    store = s3_store(settings, prefix)
    try:
        yield store
    finally:
        await store.aclose()
        await remove_prefix(settings, prefix)

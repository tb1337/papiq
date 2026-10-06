"""The S3 object store passes the contract against the configured server (Garage in the
devcontainer). Every test works below a key prefix of its own, removed afterwards."""

import secrets
from collections.abc import AsyncIterator

import aioboto3
import pytest

from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.composition.settings import Settings
from tests import probes
from tests.contracts.object_store import ObjectStoreContract


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


@pytest.fixture
async def object_store(s3_settings: Settings) -> AsyncIterator[S3ObjectStore]:
    prefix = f"tests/{secrets.token_hex(8)}/"
    store = s3_store(s3_settings, prefix)
    yield store
    await store.aclose()
    await remove_prefix(s3_settings, prefix)


class TestS3ObjectStore(ObjectStoreContract):
    pass


async def test_the_content_type_is_stored(object_store: S3ObjectStore) -> None:
    await object_store.put("a.pdf", b"%PDF", content_type="application/pdf")
    s3 = await object_store._s3()
    head = await s3.head_object(Bucket=object_store._bucket, Key=object_store._key("a.pdf"))
    assert head["ContentType"] == "application/pdf"


async def test_the_store_can_be_closed_and_reused(object_store: S3ObjectStore) -> None:
    await object_store.put("a", b"x", content_type="text/plain")
    await object_store.aclose()
    assert await object_store.get("a") == b"x"

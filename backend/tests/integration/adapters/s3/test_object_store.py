"""The S3 object store passes the contract against the configured server (Garage in the
devcontainer). Every test works below a key prefix of its own, removed afterwards."""

from collections.abc import AsyncIterator

import pytest

from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.composition.settings import Settings
from tests.contracts.object_store import ObjectStoreContract
from tests.integration.conftest import s3_test_store


@pytest.fixture
async def object_store(s3_settings: Settings) -> AsyncIterator[S3ObjectStore]:
    async with s3_test_store(s3_settings) as store:
        yield store


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

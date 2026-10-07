import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aioboto3
import pytest

from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.adapters.outbound.sql import Database, migrate
from papiq.composition.settings import Settings, load_settings
from papiq.core.ports import ObjectStore
from tests import probes
from tests.builders import SECRET_KEY, TRUSTED_PROXY
from tests.integration.adapters.sql.conftest import create_database, drop_database, postgres


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The configuration from the environment: in the devcontainer, the compose services. The
    secret key and the trusted proxies the API requires are not what these tests are about;
    test values fill in."""
    os.environ.setdefault("PAPIQ_SECRET_KEY", SECRET_KEY)
    os.environ.setdefault("PAPIQ_FORWARDED_ALLOW_IPS", TRUSTED_PROXY)
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


# The two set-ups of the end-to-end tests: SQLite with the filesystem, Postgres with S3 (Garage).
@pytest.fixture(params=["sqlite+filesystem", "postgres+s3"])
async def stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[Database, ObjectStore]]:
    if request.param == "sqlite+filesystem":
        database = Database.sqlite(tmp_path / "papiq.db")
        await migrate(database)
        yield database, FilesystemObjectStore(tmp_path / "objects")
        await database.dispose()
        return
    settings: Settings = request.getfixturevalue("settings")
    if settings.db_type != "postgres" or settings.db_host is None:
        pytest.skip("PAPIQ_DB_TYPE is not postgres")
    if not probes.postgres_answers(settings.db_host, settings.db_port):
        pytest.skip(f"Postgres not reachable at {settings.db_host}:{settings.db_port}")
    s3: Settings = request.getfixturevalue("s3_settings")
    assert settings.db_name
    name = f"{settings.db_name}_test_{secrets.token_hex(4)}"
    await create_database(settings, name)
    database = postgres(settings, name)
    try:
        await migrate(database)
        async with s3_test_store(s3) as store:
            yield database, store
    finally:
        await database.dispose()
        await drop_database(settings, name)


@pytest.fixture
def object_store(stores: tuple[Database, ObjectStore]) -> ObjectStore:
    return stores[1]

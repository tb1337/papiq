"""Checks that the services of the devcontainer (or CI) answer. Skipped when they do not.

Which services apply comes from the `PAPIQ_` variables, the same ones the application reads.
"""

import pytest

from papiq.composition.settings import Settings
from tests import probes


def test_postgres_is_reachable(settings: Settings) -> None:
    if settings.db_type != "postgres" or settings.db_host is None:
        pytest.skip("PAPIQ_DB_TYPE is not postgres")
    if not probes.postgres_answers(settings.db_host, settings.db_port):
        pytest.skip(f"Postgres not reachable at {settings.db_host}:{settings.db_port}")


def test_garage_s3_is_reachable(settings: Settings) -> None:
    if settings.storage_type != "s3" or settings.s3_endpoint_url is None:
        pytest.skip("PAPIQ_STORAGE_TYPE is not s3")
    url = str(settings.s3_endpoint_url)
    # An unauthenticated request is answered with an error status (403); any status proves
    # that the S3 API listens.
    if probes.http_status(url) is None:
        pytest.skip(f"S3 endpoint not reachable at {url}")


def test_meilisearch_is_reachable(settings: Settings) -> None:
    if settings.meilisearch_url is None:
        pytest.skip("PAPIQ_MEILISEARCH_URL is not set")
    url = str(settings.meilisearch_url)
    status = probes.meilisearch_health(url)
    if status is None:
        pytest.skip(f"Meilisearch not reachable at {url}")
    assert status == "available"

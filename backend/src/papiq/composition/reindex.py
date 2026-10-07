"""`python -m papiq.composition reindex`: rebuild the search index from the database and the
object store, in the foreground. The same rebuild the API starts as a job (`POST
/search/reindex`); the search keeps serving from the old index until the new one is swapped in.
Do not run it while a rebuild job is running."""

from papiq.composition.container import build_container, build_services
from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings
from papiq.core.services.indexing import Rebuilt


async def run_reindex(settings: Settings, *, progress: bool = False) -> Rebuilt:
    if settings.meilisearch_url is None:
        raise ConfigurationError("PAPIQ_MEILISEARCH_URL is not set: there is no search index")

    def report(done: int, total: int) -> None:
        print(f"indexed {done} of {total} documents", flush=True)

    container = build_container(settings)
    try:
        services = build_services(container, settings)
        assert services.indexing is not None and container.search_index is not None
        await container.search_index.check()
        return await services.indexing.rebuild(report if progress else None)
    finally:
        await container.aclose()

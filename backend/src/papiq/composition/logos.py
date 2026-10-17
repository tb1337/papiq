"""`fetch-logos`: queue fetching the logos of contacts from their websites."""

from papiq.composition.container import build_container, build_services
from papiq.composition.settings import Settings


async def run_fetch_logos(settings: Settings) -> int:
    """Queue a fetch for every contact without a logo that has a domain whose fetch has not
    failed; the worker fetches. Returns how many were queued. LogoFetchDisabledError without
    `PAPIQ_LOGO_FETCH`."""
    container = build_container(settings)
    try:
        return await build_services(container, settings).logos.request_all()
    finally:
        await container.aclose()

"""A stand-in for fetching logos: no network."""

from collections.abc import Mapping

from papiq.core.domain.errors import LogoFetchError
from papiq.core.ports.logos import FetchedLogo


class FakeLogoFetcher:
    """The websites it knows (`sites`: domain to the image it serves as its icon); any other
    has none. `calls` lists the domains it was asked for."""

    def __init__(self, sites: Mapping[str, bytes] | None = None) -> None:
        self.sites = dict(sites or {})
        self.calls: list[str] = []

    async def fetch(self, domain: str) -> FetchedLogo:
        self.calls.append(domain)
        if domain not in self.sites:
            raise LogoFetchError(f"{domain} declares no icon")
        return FetchedLogo(data=self.sites[domain], url=f"https://{domain}/apple-touch-icon.png")

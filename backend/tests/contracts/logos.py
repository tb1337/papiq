"""Contract suite for `LogoFetcher`. It needs the fixture `logo_fetcher`, which knows the website
`logo.example` (an apple-touch-icon of 180 px, `LOGO_PNG`) and `none.example` (a website without
an icon that fits); nothing goes to a network."""

import io

import pytest
from PIL import Image

from papiq.core.domain import media_types
from papiq.core.domain.errors import LogoFetchError
from papiq.core.ports.logos import MIN_LOGO_SIZE, LogoFetcher
from tests.contracts.images import LOGO_PNG

__all__ = ["LOGO_PNG", "LogoFetcherContract"]


class LogoFetcherContract:
    async def test_fetches_the_icon_of_a_website(self, logo_fetcher: LogoFetcher) -> None:
        logo = await logo_fetcher.fetch("logo.example")
        assert logo.url.startswith("https://")
        assert media_types.detect_image(logo.data) is not None
        with Image.open(io.BytesIO(logo.data)) as found:
            assert max(found.size) >= MIN_LOGO_SIZE

    async def test_a_website_without_a_fitting_icon_has_none(
        self, logo_fetcher: LogoFetcher
    ) -> None:
        with pytest.raises(LogoFetchError):
            await logo_fetcher.fetch("none.example")

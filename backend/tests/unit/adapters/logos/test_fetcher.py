"""The logo fetcher over httpx2 against a stand-in for the web (`web.py`), with a fixed
resolver: it passes the contract, takes the largest icon a website declares, and keeps to
public addresses, HTTPS, few redirects, small and quick answers. No request leaves."""

import asyncio
import gzip
import io
import struct
import time

import httpx2
import pytest
from PIL import Image

from papiq.adapters.outbound.logos import HttpLogoFetcher
from papiq.adapters.outbound.logos.fetcher import MAX_REDIRECTS, _pixels
from papiq.core.domain.errors import LogoFetchError
from tests.contracts.images import LOGO_PNG, image
from tests.contracts.logos import LogoFetcherContract
from tests.unit.adapters.logos.web import (
    OTHER_PUBLIC,
    PUBLIC,
    Web,
    answer,
    gzipped,
    html,
    redirect,
)

APPLE = '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'


def png(size: int) -> bytes:
    return image((size, size))


def ico(*sizes: int) -> bytes:
    out = io.BytesIO()
    largest = max(sizes)
    Image.new("RGBA", (largest, largest), (1, 2, 3, 255)).save(
        out, "ICO", sizes=[(size, size) for size in sizes]
    )
    return out.getvalue()


def size_of(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as found:
        return max(found.size)


@pytest.fixture
def web() -> Web:
    return Web()


def fetcher(web: Web, **options: float) -> HttpLogoFetcher:
    return HttpLogoFetcher(resolver=web.resolve, transport=web.transport, **options)  # type: ignore[arg-type]


@pytest.fixture
def logo_fetcher(web: Web) -> HttpLogoFetcher:
    web.site("logo.example")
    web.serve("logo.example", "/", html(f"<head>{APPLE}</head>"))
    web.serve("logo.example", "/apple-touch-icon.png", answer("image/png", LOGO_PNG))
    web.site("none.example")
    web.serve("none.example", "/", html('<link rel="icon" href="/i.svg">'))
    return fetcher(web)


class TestHttpLogoFetcher(LogoFetcherContract):
    pass


# --- which icon -------------------------------------------------------------------------------


async def test_takes_the_largest_icon_the_site_declares(web: Web) -> None:
    web.site("nord.example")
    web.site("cdn.nord.example", OTHER_PUBLIC)
    page = f"""<html><head>
        <link rel="icon" href="/favicon-32.png" sizes="32x32">
        {APPLE}
        <link rel="icon" href="/logo.svg" type="image/svg+xml">
        <link rel="manifest" href="https://cdn.nord.example/app.webmanifest">
    </head></html>"""
    manifest = (
        b'{"icons": [{"src": "/192.png", "sizes": "192x192"},'
        b' {"src": "/512.png", "sizes": "512x512"}]}'
    )
    web.serve("nord.example", "/", html(page))
    web.serve("cdn.nord.example", "/app.webmanifest", answer("application/manifest+json", manifest))
    web.serve("cdn.nord.example", "/512.png", answer("image/png", png(512)))
    web.serve("cdn.nord.example", "/192.png", answer("image/png", png(192)))
    web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
    logo = await fetcher(web).fetch("nord.example")
    assert logo.url == "https://cdn.nord.example/512.png"
    assert size_of(logo.data) == 512
    # Large enough at once: nothing else is fetched, no SVG, no small icon.
    paths = [request.url.path for request in web.requests]
    assert paths == ["/", "/app.webmanifest", "/512.png"]
    assert web.connected == {
        "nord.example": [PUBLIC],
        "cdn.nord.example": [OTHER_PUBLIC, OTHER_PUBLIC],
    }


async def test_a_declared_size_is_checked_and_the_favicon_is_the_last_resort(web: Web) -> None:
    web.site("nord.example")
    web.serve(
        "nord.example",
        "/",
        html('<link rel="icon" href="/big.png" sizes="256x256"><link rel="icon" href="/x.png">'),
    )
    web.serve("nord.example", "/big.png", answer("image/png", png(48)))  # claims 256
    web.serve("nord.example", "/x.png", answer("image/png", png(16)))
    web.serve("nord.example", "/favicon.ico", answer("image/x-icon", ico(16, 32, 64)))
    logo = await fetcher(web).fetch("nord.example")
    assert logo.url == "https://nord.example/favicon.ico"
    assert size_of(logo.data) == 64


async def test_keeps_the_largest_of_icons_below_128_pixels(web: Web) -> None:
    web.site("nord.example")
    web.serve(
        "nord.example",
        "/",
        html('<link rel="icon" href="/a.png"><link rel="icon" href="/b.png">'),
    )
    web.serve("nord.example", "/a.png", answer("image/png", png(64)))
    web.serve("nord.example", "/b.png", answer("image/png", png(96)))
    web.serve("nord.example", "/favicon.ico", answer("image/x-icon", ico(16)))
    logo = await fetcher(web).fetch("nord.example")
    assert (logo.url, size_of(logo.data)) == ("https://nord.example/b.png", 96)


async def test_no_icon_of_64_pixels_is_no_logo(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", html('<link rel="icon" href="/i.png" sizes="32x32">'))
    web.serve("nord.example", "/i.png", answer("image/png", png(32)))
    web.serve("nord.example", "/favicon.ico", answer("image/x-icon", ico(16, 32)))
    with pytest.raises(LogoFetchError, match="no raster icon"):
        await fetcher(web).fetch("nord.example")
    # The icon declared too small is not even fetched.
    assert "/i.png" not in [request.url.path for request in web.requests]


async def test_tries_www_when_the_domain_has_no_website(web: Web) -> None:
    web.site("www.nord.example")
    web.serve("www.nord.example", "/", html(APPLE))
    web.serve("www.nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
    logo = await fetcher(web).fetch("nord.example")
    assert logo.url == "https://www.nord.example/apple-touch-icon.png"
    assert web.lookups[0] == "nord.example"


async def test_without_a_page_the_favicon_of_the_domain(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/favicon.ico", answer("image/vnd.microsoft.icon", ico(128)))
    logo = await fetcher(web).fetch("nord.example")
    assert logo.url == "https://nord.example/favicon.ico"


async def test_takes_at_most_four_icons(web: Web) -> None:
    web.site("nord.example")
    links = "".join(
        f'<link rel="icon" href="/{n}.png" sizes="{200 - n}x{200 - n}">' for n in range(8)
    )
    web.serve("nord.example", "/", html(links))
    with pytest.raises(LogoFetchError):
        await fetcher(web).fetch("nord.example")
    assert len(web.requests) == 1 + 4


async def test_a_long_page_is_read_as_far_as_the_limit(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", html(f"<head>{APPLE}</head>" + "x" * 3_000_000))
    web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
    logo = await fetcher(web).fetch("nord.example")
    assert size_of(logo.data) == 180


async def test_gzip_is_decoded_within_the_limit(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", gzipped("text/html", APPLE.encode()))
    web.serve("nord.example", "/apple-touch-icon.png", gzipped("image/png", png(180)))
    assert size_of((await fetcher(web).fetch("nord.example")).data) == 180
    assert web.requests[0].headers["accept-encoding"] == "identity"
    # A bomb: a few kilobytes that would be gigabytes.
    web.serve("nord.example", "/apple-touch-icon.png", gzipped("image/png", bytes(50_000_000)))
    with pytest.raises(LogoFetchError, match="larger than"):
        await fetcher(web).get("https://nord.example/apple-touch-icon.png", {"image/png"})
    web.serve("nord.example", "/br", answer("image/png", b"x", headers={"content-encoding": "br"}))
    with pytest.raises(LogoFetchError, match="encoded"):
        await fetcher(web).get("https://nord.example/br", {"image/png"})


# --- the guard ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    ["10.0.0.7", "192.168.1.1", "127.0.0.1", "169.254.169.254", "100.64.12.1", "fd00::1",
     "::1", "fe80::1", "::ffff:192.168.1.1", "::ffff:127.0.0.1", "64:ff9b::a00:7", "0.0.0.0"],
)  # fmt: skip
async def test_refuses_a_site_with_an_address_that_is_not_public(web: Web, address: str) -> None:
    web.site("nord.example", address)
    web.site("www.nord.example", address)
    web.serve("nord.example", "/", html(APPLE))
    with pytest.raises(LogoFetchError):
        await fetcher(web).fetch("nord.example")
    assert web.requests == []


async def test_every_address_of_a_name_must_be_public(web: Web) -> None:
    web.site("nord.example", PUBLIC, "10.0.0.7")
    web.serve("nord.example", "/", html(APPLE))
    with pytest.raises(LogoFetchError, match="not public"):
        await fetcher(web).get("https://nord.example/", {"text/html"})
    assert web.requests == []


async def test_connects_to_the_address_it_checked(web: Web) -> None:
    """DNS rebinding: the name resolves to a public address when it is checked, and to a
    private one right after. The request goes to the checked address; a second request
    resolves again and is refused."""
    web.site("nord.example")
    web.next_answers["nord.example"] = [[PUBLIC], ["127.0.0.1"]]
    web.serve("nord.example", "/", html(f'{APPLE}<link rel="manifest" href="/m.json">'))
    web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
    logo = await fetcher(web).fetch("nord.example")
    assert logo.url == "https://nord.example/apple-touch-icon.png"
    # The manifest's request met the private answer and was not made.
    assert [request.url.path for request in web.requests] == ["/", "/apple-touch-icon.png"]
    assert set(web.connected["nord.example"]) == {PUBLIC}
    assert web.lookups.count("nord.example") == 3


async def test_a_redirect_to_a_private_address_is_refused(web: Web) -> None:
    web.site("nord.example")
    web.site("intern.nord.example", "10.0.0.7")
    web.serve("nord.example", "/", redirect("https://intern.nord.example/"))
    web.serve("intern.nord.example", "/", html(APPLE))
    with pytest.raises(LogoFetchError, match="not public"):
        await fetcher(web).get("https://nord.example/", {"text/html"})
    assert web.hosts() == ["nord.example"]


@pytest.mark.parametrize(
    "location",
    [
        "http://nord.example/plain",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://[::ffff:10.0.0.1]/",
        "https://169.254.169.254/latest/meta-data/",
        "https://nord.example:8443/",
        "https://user:secret@nord.example/",
        "ftp://nord.example/",
        "file:///etc/passwd",
    ],
)
async def test_a_redirect_is_checked_like_the_first_request(web: Web, location: str) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", redirect(location))
    with pytest.raises(LogoFetchError):
        await fetcher(web).get("https://nord.example/", {"text/html"})
    assert len(web.requests) == 1


async def test_follows_at_most_three_redirects(web: Web) -> None:
    web.site("nord.example")
    for step in range(MAX_REDIRECTS + 1):
        web.serve("nord.example", f"/{step}", redirect(f"/{step + 1}", 301 + step % 2))
    web.serve("nord.example", f"/{MAX_REDIRECTS}", html("ok"))
    found = await fetcher(web).get("https://nord.example/0", {"text/html"})
    assert (str(found.url), found.text) == (f"https://nord.example/{MAX_REDIRECTS}", "ok")
    web.serve("nord.example", f"/{MAX_REDIRECTS}", redirect("/end"))
    web.serve("nord.example", "/end", html("too far"))
    with pytest.raises(LogoFetchError, match="redirects"):
        await fetcher(web).get("https://nord.example/0", {"text/html"})
    assert web.requests[-1].url.path == f"/{MAX_REDIRECTS}"


async def test_refuses_what_is_too_large(web: Web) -> None:
    web.site("nord.example")
    big = png(64) + bytes(1024 * 1024)
    web.serve("nord.example", "/announced", answer("image/png", big))
    web.serve(
        "nord.example",
        "/unannounced",
        answer("image/png", big, headers={"content-length": "100"}),
    )
    for path in ("/announced", "/unannounced"):
        with pytest.raises(LogoFetchError, match="larger than"):
            await fetcher(web).get(f"https://nord.example{path}", {"image/png"})
    web.serve("nord.example", "/", html(APPLE))
    web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", big))
    with pytest.raises(LogoFetchError):
        await fetcher(web).fetch("nord.example")


async def test_refuses_what_is_too_slow(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/slow", answer("image/png", png(180), delay=0.5, chunk=100))
    with pytest.raises(LogoFetchError, match="no answer within"):
        await fetcher(web, timeout=0.05).get("https://nord.example/slow", {"image/png"})

    async def hang(host: str) -> list[str]:
        await asyncio.sleep(1)
        return [PUBLIC]

    slow_names = HttpLogoFetcher(resolver=hang, transport=web.transport, deadline=0.05)
    with pytest.raises(LogoFetchError, match="no logo within"):
        await slow_names.fetch("nord.example")


async def test_takes_only_the_content_types_it_asks_for(web: Web) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", html(APPLE))
    web.serve("nord.example", "/apple-touch-icon.png", answer("text/html", png(180)))
    web.serve("nord.example", "/favicon.ico", answer("image/svg+xml", b"<svg/>"))
    with pytest.raises(LogoFetchError):
        await fetcher(web).fetch("nord.example")
    web.serve("nord.example", "/page.png", answer("image/png", png(180)))
    with pytest.raises(LogoFetchError, match="content type"):
        await fetcher(web).get("https://nord.example/page.png", {"text/html"})


async def test_sends_no_cookies_and_ignores_proxies_of_the_environment(
    web: Web, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    web.site("nord.example")
    web.serve(
        "nord.example",
        "/",
        html(APPLE, headers={"set-cookie": "session=secret; Secure; Path=/"}),
    )
    web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
    await fetcher(web).fetch("nord.example")
    # The transport saw both (the web stand-in checks that no cookie came along).
    assert len(web.requests) == 2
    assert web.requests[0].headers["user-agent"] == "Papiq"


@pytest.mark.parametrize(
    "domain", ["localhost", "127.0.0.1", "nord", "a b.example", "nord.example/x", "-nord.example"]
)
async def test_fetches_only_for_a_domain(web: Web, domain: str) -> None:
    with pytest.raises(LogoFetchError, match="no domain"):
        await fetcher(web).fetch(domain)
    assert web.lookups == [] and web.requests == []


async def test_a_public_address_as_host_is_named_as_it_is(web: Web) -> None:
    web.site(OTHER_PUBLIC)  # an address resolves to itself, no name server is asked
    web.serve(OTHER_PUBLIC, "/", html("ok"))
    found = await fetcher(web).get(f"https://[{OTHER_PUBLIC}]/", {"text/html"})
    assert found.text == "ok"
    assert web.requests[0].headers["host"] == f"[{OTHER_PUBLIC}]"
    assert web.lookups == []


async def test_a_name_that_does_not_resolve_and_a_failing_server(web: Web) -> None:
    with pytest.raises(LogoFetchError, match="cannot be resolved"):
        await fetcher(web).get("https://gone.example/", {"text/html"})
    web.site("nord.example")

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    web.serve("nord.example", "/", broken)
    with pytest.raises(LogoFetchError, match="cannot connect"):
        await fetcher(web).get("https://nord.example/", {"text/html"})
    web.serve("nord.example", "/", html("gone", status=410))
    with pytest.raises(LogoFetchError, match="status 410"):
        await fetcher(web).get("https://nord.example/", {"text/html"})


# --- what a hostile website may send ------------------------------------------------------------


async def test_reads_pages_as_utf8_whatever_charset_they_name(web: Web) -> None:
    """A codec the server names could be slow (punycode) or fail (idna)."""
    web.site("nord.example")
    for charset in ("punycode", "idna", "undefined"):
        web.serve(
            "nord.example",
            "/",
            answer(f"text/html; charset={charset}", APPLE.encode() + b"x" * 500_000),
        )
        web.serve("nord.example", "/apple-touch-icon.png", answer("image/png", png(180)))
        started = time.monotonic()
        logo = await fetcher(web).fetch("nord.example")
        assert logo.url.endswith("/apple-touch-icon.png")
        assert time.monotonic() - started < 2


async def test_a_favicon_is_measured_by_its_directory_not_decoded(web: Web) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (256, 256), (1, 2, 3, 255)).save(out, "ICO", sizes=[(16, 16), (256, 256)])
    started = time.monotonic()
    assert _pixels(out.getvalue()) == 256
    big = io.BytesIO()
    Image.new("L", (4000, 4000)).save(big, "PNG")  # one picture larger than its directory says
    directory = struct.pack("<HHH", 0, 1, 1) + struct.pack(
        "<BBBBHHII", 0, 0, 0, 0, 1, 32, len(big.getvalue()), 22
    )
    assert _pixels(directory + big.getvalue()) == 256
    assert time.monotonic() - started < 0.5
    assert _pixels(struct.pack("<HHH", 0, 1, 70) + bytes(70 * 16)) is None  # too many pictures
    assert _pixels(png(5000)) is None  # larger than a logo is made from


async def test_gzip_ends_with_its_stream_and_counts_as_sent(web: Web) -> None:
    web.site("nord.example")
    body = gzip.compress(png(180)) + bytes(5_000_000)  # rubbish after the stream
    web.serve(
        "nord.example", "/trailing", answer("image/png", body, headers={"content-encoding": "gzip"})
    )
    found = await fetcher(web).get("https://nord.example/trailing", {"image/png"})
    assert found.body == png(180)
    # Empty stored blocks: nothing decoded, but more than the limit sent.
    empty = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + b"\x00\x00\x00\xff\xff" * 300_000
    web.serve(
        "nord.example", "/empty", answer("image/png", empty, headers={"content-encoding": "gzip"})
    )
    with pytest.raises(LogoFetchError, match="larger than"):
        await fetcher(web).get("https://nord.example/empty", {"image/png"})


async def test_what_breaks_a_parser_is_a_failed_fetch(
    web: Web, monkeypatch: pytest.MonkeyPatch
) -> None:
    web.site("nord.example")
    web.serve("nord.example", "/", html(APPLE))

    def broken(html: str, url: str) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("papiq.adapters.outbound.logos.fetcher.page_icons", broken)
    with pytest.raises(LogoFetchError, match="RuntimeError"):
        await fetcher(web).fetch("nord.example")

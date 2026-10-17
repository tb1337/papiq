"""Fetches the logo of a contact from its own website over httpx2, guarded against server-side
request forgery: the domain comes from identifiers learned from documents.

Every request (the page, the manifest, each icon, each redirect):

- goes over HTTPS to port 443 only, without credentials in the address;
- resolves the host name and refuses it unless every address it has is public
  (`addresses.is_public`), then connects to the address it checked, with the host name as
  `Host` and for TLS (SNI and the certificate), so that a second answer of the name server
  cannot lead elsewhere (DNS rebinding);
- follows at most `MAX_REDIRECTS` redirects itself, each checked like the first;
- takes at most `TIMEOUT` with its redirects, reads at most `MAX_SIZE` bytes as sent and as
  decoded (a page longer than that is read as far as that, its head is what counts; a longer
  image is refused), accepts only the content types it asks for, and no encoding but gzip;
- sends no cookies (each request has a client of its own) and ignores the environment's
  proxies and `.netrc` (`trust_env=False`).

The whole fetch takes at most `DEADLINE` and `MAX_TRIES` icons. Pages and manifests are read
as UTF-8 whatever charset the server names (the link tags are ASCII; a codec it chooses could
be slow). An icon's size comes from its header alone, in a thread (of an ICO from its
directory: Pillow would decode the largest picture to open it). Failures are LogoFetchError
with a short reason; no other error leaves.
"""

import asyncio
import io
import logging
import re
import ssl
import zlib
from collections.abc import Collection
from dataclasses import dataclass

import httpx2
from PIL import Image, UnidentifiedImageError

from papiq.adapters.outbound.logos.addresses import Resolver, is_public, literal, system_resolver
from papiq.adapters.outbound.logos.icons import Candidate, manifest_icons, page_icons, ranked
from papiq.core.domain import media_types
from papiq.core.domain.errors import LogoFetchError
from papiq.core.ports.images import LOGO_SIZE
from papiq.core.ports.logos import MIN_LOGO_SIZE, FetchedLogo

log = logging.getLogger(__name__)

TIMEOUT = 10.0  # seconds per request, with its redirects
CONNECT_TIMEOUT = 5.0
DEADLINE = 30.0  # seconds for a whole fetch
MAX_SIZE = 1024 * 1024
MAX_REDIRECTS = 3
MAX_TRIES = 4  # icons fetched at most

PAGES = frozenset({"text/html", "application/xhtml+xml"})
MANIFESTS = frozenset({"application/manifest+json", "application/json"})
IMAGES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/x-icon", "image/vnd.microsoft.icon"}
)
_REDIRECTS = {301, 302, 303, 307, 308}
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_IMAGE_FORMATS = ("PNG", "JPEG", "WEBP")
_MAX_PIXELS = 4096 * 4096  # as the normaliser takes
_MAX_ICON_ENTRIES = 64


@dataclass(frozen=True)
class Answer:
    url: httpx2.URL  # the last one, after redirects
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class HttpLogoFetcher:
    def __init__(
        self,
        *,
        user_agent: str = "Papiq",
        resolver: Resolver = system_resolver,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT,
        deadline: float = DEADLINE,
        max_size: int = MAX_SIZE,
    ) -> None:
        """`resolver` and `transport` replace the name server and the network in tests."""
        self._user_agent = user_agent
        self._resolve = resolver
        self._transport = transport
        self._timeout = timeout
        self._deadline = deadline
        self._max_size = max_size

    async def fetch(self, domain: str) -> FetchedLogo:
        if not _DOMAIN.fullmatch(domain):
            raise LogoFetchError(f"'{domain}' is no domain")
        try:
            async with asyncio.timeout(self._deadline):
                return await self._fetch(domain)
        except TimeoutError:
            raise LogoFetchError(f"no logo within {self._deadline:g} seconds") from None
        except LogoFetchError:
            raise
        except Exception as error:  # what a website's answer may break in a parser
            log.warning("logo: the website's answer broke the fetch", exc_info=True)
            raise LogoFetchError(f"the website cannot be read: {type(error).__name__}") from None

    async def _fetch(self, domain: str) -> FetchedLogo:
        page: Answer | None = None
        for host in (domain, f"www.{domain}"):
            try:
                page = await self.get(f"https://{host}/", PAGES, whole=False)
                break
            except LogoFetchError as error:
                log.info("logo: the page cannot be read", extra={"host": host, "error": str(error)})
        candidates: list[Candidate] = []
        base = httpx2.URL(f"https://{domain}/") if page is None else page.url
        if page is not None:
            icons, manifest = page_icons(page.text, str(page.url))
            candidates += icons
            if manifest is not None:
                try:
                    answer = await self.get(manifest, MANIFESTS)
                    candidates += manifest_icons(answer.text, str(answer.url))
                except LogoFetchError as error:
                    log.info("logo: the manifest cannot be read", extra={"error": str(error)})
        best: tuple[int, FetchedLogo] | None = None
        for url in ranked(candidates, str(base.join("/favicon.ico")))[:MAX_TRIES]:
            try:
                answer = await self.get(url, IMAGES)
            except LogoFetchError as error:
                log.info("logo: an icon cannot be read", extra={"error": str(error)})
                continue
            size = await asyncio.to_thread(_pixels, answer.body)
            if size is None or size < MIN_LOGO_SIZE:
                continue
            if best is None or size > best[0]:
                best = (size, FetchedLogo(data=answer.body, url=str(answer.url)))
            if size >= LOGO_SIZE:
                break
        if best is None:
            raise LogoFetchError(
                f"{domain} declares no raster icon of at least {MIN_LOGO_SIZE} pixels"
            )
        return best[1]

    async def get(self, url: str, accept: Collection[str], *, whole: bool = True) -> Answer:
        """GET `url` as the module says, following redirects; `accept`: the content types
        taken. `whole`: refuse a body larger than the limit (else keep its start)."""
        try:
            async with asyncio.timeout(self._timeout):
                return await self._follow(_https(httpx2.URL(url)), accept, whole)
        except TimeoutError:
            raise LogoFetchError(f"no answer within {self._timeout:g} seconds") from None
        except httpx2.InvalidURL:
            raise LogoFetchError("an address that cannot be read") from None
        except httpx2.HTTPError as failure:
            raise LogoFetchError(_describe(failure)) from None
        except (ssl.SSLError, OSError) as failure:
            raise LogoFetchError(f"cannot connect: {type(failure).__name__}") from None

    async def _follow(self, target: httpx2.URL, accept: Collection[str], whole: bool) -> Answer:
        for _ in range(MAX_REDIRECTS + 1):
            host = target.raw_host.decode("ascii")
            address = await self._address(host)
            async with httpx2.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx2.Timeout(self._timeout, connect=min(CONNECT_TIMEOUT, self._timeout)),
            ) as client:
                request = client.build_request(
                    "GET",
                    target.copy_with(host=address),
                    headers={
                        "Host": f"[{host}]" if ":" in host else host,
                        "User-Agent": self._user_agent,
                        "Accept": ", ".join(sorted(accept)),
                        "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": host},
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise LogoFetchError("a redirect without a target")
                        target = _https(target.join(location))
                        continue
                    if response.status_code != 200:
                        raise LogoFetchError(f"status {response.status_code}")
                    kind = response.headers.get("content-type", "").partition(";")[0]
                    kind = kind.strip().lower()
                    if kind not in accept:
                        raise LogoFetchError(f"content type '{kind}'")
                    body = await self._body(response, whole)
                    return Answer(target, kind, body)
                finally:
                    await response.aclose()
        raise LogoFetchError(f"more than {MAX_REDIRECTS} redirects")

    async def _address(self, host: str) -> str:
        """The address to connect to: the host's own, or the first its name resolves to, once
        every one of them is public."""
        own = literal(host)
        if own is not None:
            addresses: list[str] = [own]
        else:
            try:
                addresses = list(await self._resolve(host))
            except OSError:
                raise LogoFetchError(f"{host} cannot be resolved") from None
        if not addresses:
            raise LogoFetchError(f"{host} cannot be resolved")
        if not all(is_public(address) for address in addresses):
            raise LogoFetchError(f"{host} has an address that is not public")
        return addresses[0]

    async def _body(self, response: httpx2.Response, whole: bool) -> bytes:
        limit = self._max_size
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("identity", "gzip"):
            raise LogoFetchError(f"encoded as '{encoding}'")
        length = response.headers.get("content-length", "")
        if whole and encoding == "identity" and length.isdigit() and int(length) > limit:
            raise LogoFetchError(f"larger than {limit} bytes")
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        body = bytearray()
        sent = 0
        over = False
        try:
            async for chunk in response.aiter_raw():
                sent += len(chunk)
                if decoder is not None:
                    chunk = decoder.decompress(chunk, limit + 1 - len(body))
                    over = bool(decoder.unconsumed_tail)
                body += chunk
                # The bytes as sent count too: a stream of empty blocks is no shorter.
                over = over or len(body) > limit or sent > limit
                if over or (decoder is not None and decoder.eof):
                    break
        except zlib.error:
            raise LogoFetchError("a body that cannot be decoded") from None
        if over:
            if whole:
                raise LogoFetchError(f"larger than {limit} bytes")
            del body[limit:]
        return bytes(body)


def _https(url: httpx2.URL) -> httpx2.URL:
    if url.scheme != "https":
        raise LogoFetchError(f"not HTTPS: '{url.scheme}'")
    if url.port not in (None, 443):
        raise LogoFetchError(f"port {url.port}, not that of HTTPS")
    if url.userinfo:
        raise LogoFetchError("credentials in the address")
    if not url.raw_host:
        raise LogoFetchError("an address without a host")
    return url


def _pixels(data: bytes) -> int | None:
    """The longer side of a raster image a logo can be made from, by its header alone; of an
    ICO the largest its directory names (opening it, Pillow would decode a picture). None for
    anything else, and for an image larger than a logo is made from."""
    kind = media_types.detect_image(data[: media_types.SNIFF_SIZE])
    if kind is None:
        return None
    if kind == media_types.ICO:
        return _icon_size(data)
    try:
        with Image.open(io.BytesIO(data), formats=_IMAGE_FORMATS) as image:
            width, height = image.size
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
    ):
        return None
    return max(width, height) if 0 < width * height <= _MAX_PIXELS else None


def _icon_size(data: bytes) -> int | None:
    """The largest side an ICO's directory names (a byte each, 0 for 256)."""
    count = int.from_bytes(data[4:6], "little")
    if not 0 < count <= _MAX_ICON_ENTRIES or len(data) < 6 + 16 * count:
        return None
    entries = [data[6 + 16 * index : 8 + 16 * index] for index in range(count)]
    return max(side or 256 for entry in entries for side in entry)


def _describe(failure: httpx2.HTTPError) -> str:
    cause: BaseException | None = failure
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "the TLS certificate was not accepted"
        if isinstance(cause, ssl.SSLError):
            return "TLS handshake failed"
        cause = cause.__cause__ or cause.__context__
    if isinstance(failure, httpx2.TimeoutException):
        return "no answer in time"
    if isinstance(failure, httpx2.ConnectError):
        return "cannot connect"
    return f"request failed: {type(failure).__name__}"

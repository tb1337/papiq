"""The web UI below `/ui`: files of the build, the page fallback, caching and the API's paths."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.rest.ui import IMMUTABLE, REVALIDATE, SECURITY_HEADERS
from papiq.composition.container import build_memory_container, build_services
from tests.unit.adapters.rest.conftest import make_app

INDEX = "<!doctype html><title>papiq</title>"
SCRIPT = "export const x = 1;"
SCRIPT_PATH = "_app/immutable/entry/start.Bx1y2z.js"


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """A small build: the page, a hashed script, a plain file and a hidden one."""
    root = tmp_path / "build"
    (root / "_app" / "immutable" / "entry").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / SCRIPT_PATH).write_text(SCRIPT, encoding="utf-8")
    (root / "_app" / "version.json").write_text('{"version":"1"}', encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


async def client_for(directory: Path | None) -> httpx2.AsyncClient:
    container = build_memory_container()
    app = make_app(container, build_services(container), ui_directory=directory)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://papiq")


@pytest.fixture
async def client(build: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async with await client_for(build) as client:
        yield client


def assert_secured(response: httpx2.Response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


async def test_the_start_page_leads_to_the_ui(client: httpx2.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/ui/"
    response = await client.get("/ui")
    assert response.status_code == 308
    assert response.headers["location"] == "/ui/"


@pytest.mark.parametrize("path", ["/ui/", "/ui/index.html"])
async def test_the_page_is_revalidated(client: httpx2.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == REVALIDATE
    assert response.headers["etag"]
    assert_secured(response)


async def test_hashed_files_are_cached_for_good(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/ui/{SCRIPT_PATH}")
    assert response.status_code == 200
    assert response.text == SCRIPT
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == IMMUTABLE
    assert_secured(response)


async def test_empty_segments_change_nothing(client: httpx2.AsyncClient) -> None:
    hashed = await client.get(f"/ui//{SCRIPT_PATH}")
    assert hashed.headers["cache-control"] == IMMUTABLE
    missing = await client.get("/ui//_app/missing")
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/ui/favicon.svg", "/ui/_app/version.json"])
async def test_other_files_are_revalidated(client: httpx2.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == REVALIDATE


@pytest.mark.parametrize(
    "path", ["/ui/documents", "/ui/documents/", "/ui/admin/users", "/ui/login?next=%2Fui%2F"]
)
async def test_page_paths_get_the_page(client: httpx2.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["cache-control"] == REVALIDATE
    assert_secured(response)


@pytest.mark.parametrize(
    "path",
    [
        "/ui/missing.js",
        "/ui/documents/report.pdf",
        "/ui/_app/immutable/entry/gone.js",
        "/ui/_app/missing",
        "/ui/.env",
        "/ui/x/.git/config",
        "/ui/../outside.txt",
        "/ui/%2e%2e/outside.txt",
        "/ui/%2E%2E/%2E%2E/etc/passwd",
        "/ui/..%2foutside.txt",
        "/ui/..%5coutside.txt",
    ],
)
async def test_missing_and_forbidden_files_are_not_found(
    client: httpx2.AsyncClient, path: str
) -> None:
    response = await client.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert "outside" not in response.text
    assert "SECRET" not in response.text


async def test_links_out_of_the_build_are_not_followed(
    build: Path, client: httpx2.AsyncClient
) -> None:
    (build / "link.txt").symlink_to(build.parent / "outside.txt")
    response = await client.get("/ui/link.txt")
    assert response.status_code == 404


async def test_head_has_no_body(client: httpx2.AsyncClient) -> None:
    response = await client.head("/ui/documents")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(INDEX))


@pytest.mark.parametrize("path", ["/ui/", "/ui/documents", "/"])
async def test_only_reading_is_allowed(client: httpx2.AsyncClient, path: str) -> None:
    response = await client.post(path)
    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Method Not Allowed"


async def test_an_unchanged_file_is_not_sent_again(client: httpx2.AsyncClient) -> None:
    first = await client.get("/ui/favicon.svg")
    by_tag = await client.get("/ui/favicon.svg", headers={"If-None-Match": first.headers["etag"]})
    assert by_tag.status_code == 304
    assert by_tag.content == b""
    assert by_tag.headers["etag"] == first.headers["etag"]
    assert by_tag.headers["cache-control"] == REVALIDATE
    by_date = await client.get(
        "/ui/favicon.svg", headers={"If-Modified-Since": first.headers["last-modified"]}
    )
    assert by_date.status_code == 304
    other = await client.get("/ui/favicon.svg", headers={"If-None-Match": '"other"'})
    assert other.status_code == 200
    broken = await client.get("/ui/favicon.svg", headers={"If-Modified-Since": "yesterday"})
    assert broken.status_code == 200


async def test_the_api_keeps_its_paths(client: httpx2.AsyncClient) -> None:
    health = await client.get(f"{PREFIX}/health")
    assert health.headers["content-type"] == "application/json"
    unknown = await client.get(f"{PREFIX}/does-not-exist")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"] == "application/problem+json"
    assert "Content-Security-Policy" not in unknown.headers


async def test_without_a_build_there_is_no_ui() -> None:
    async with await client_for(None) as client:
        for path in ("/", "/ui", "/ui/", "/ui/documents"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"] == "application/problem+json"

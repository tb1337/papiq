"""The web UI: the built single-page app (`web/build`), served below `/ui` next to the API.

Files of the build are served as they are; every other page path gets `index.html`, and the app
routes in the browser. Hashed files below `_app/immutable/` never change and may be cached for
good; everything else is revalidated (`ETag`, `Last-Modified`, 304). The content security policy
comes from the page itself (a meta tag with the hashes of its start script); the headers add
what a meta tag cannot carry.
"""

import os
from email.utils import parsedate_to_datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from papiq.adapters.inbound.rest.problems import problem

UI_PREFIX = "/ui"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
# Files the build writes with a content hash in their name.
_IMMUTABLE_DIRECTORY = "_app/immutable/"
# The app's own files; a missing one is an error, not a page.
_APP_DIRECTORY = "_app/"


def ui_routes(directory: Path) -> list[BaseRoute]:
    """`/` and `/ui` lead to `/ui/`; below it the files of `directory` or its `index.html`."""
    root = directory.resolve()
    index = root / "index.html"

    async def start(request: Request) -> Response:
        return RedirectResponse(f"{UI_PREFIX}/", status_code=302)

    async def without_slash(request: Request) -> Response:
        return RedirectResponse(f"{UI_PREFIX}/", status_code=308)

    async def serve(request: Request) -> Response:
        path: str = request.path_params["path"]
        found = await run_in_threadpool(_find, root, path)
        if found is not None:
            cache = IMMUTABLE if path.startswith(_IMMUTABLE_DIRECTORY) else REVALIDATE
            return await _file(request, found, cache)
        if _is_page(path):
            return await _file(request, index, REVALIDATE)
        return _secured(problem(404, "There is no such file of the web UI."))

    return [
        Route("/", start, methods=["GET"], include_in_schema=False),
        Route(UI_PREFIX, without_slash, methods=["GET"], include_in_schema=False),
        Route(f"{UI_PREFIX}/{{path:path}}", serve, methods=["GET"], include_in_schema=False),
    ]


def _find(root: Path, path: str) -> Path | None:
    """The file for `path` (already URL-decoded) inside `root`, or None. Hidden names, dot
    segments and links out of `root` count as missing."""
    parts = [part for part in path.split("/") if part]
    if not parts or any(part.startswith(".") or "\\" in part or "\0" in part for part in parts):
        return None
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _is_page(path: str) -> bool:
    """A path of the app's pages: not below `_app/`, no file extension, nothing hidden."""
    parts = [part for part in path.split("/") if part]
    if path.startswith(_APP_DIRECTORY) or any(part.startswith(".") for part in parts):
        return False
    return not parts or "." not in parts[-1]


async def _file(request: Request, file: Path, cache: str) -> Response:
    stat = await run_in_threadpool(os.stat, file)
    headers = {"Cache-Control": cache}
    response = FileResponse(file, stat_result=stat, headers=headers)
    if _not_modified(request, response):
        kept = {
            name: value
            for name, value in response.headers.items()
            if name in ("etag", "last-modified", "cache-control")
        }
        return _secured(Response(status_code=304, headers=kept))
    return _secured(response)


def _not_modified(request: Request, response: Response) -> bool:
    """Whether the browser's copy is current (RFC 9110 13.1: `If-None-Match` wins)."""
    etag = response.headers.get("etag")
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
        return "*" in tags or (etag is not None and etag.removeprefix("W/") in tags)
    since = request.headers.get("if-modified-since")
    modified = response.headers.get("last-modified")
    if since is None or modified is None:
        return False
    try:
        return parsedate_to_datetime(modified) <= parsedate_to_datetime(since)
    except (TypeError, ValueError):
        return False


def _secured(response: Response) -> Response:
    response.headers.update(SECURITY_HEADERS)
    return response

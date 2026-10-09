"""Writing to Papiq through its REST API, with an admin's API token. Nothing else is used."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx2

API = "/api/v1"
RETRY_STATUS = {500, 502, 503, 504}
DELAYS = (1.0, 3.0, 10.0)


class ApiError(Exception):
    """A request Papiq refused. `existing` is the document a duplicate upload collided with."""

    def __init__(self, status: int, detail: str, existing: str | None = None) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail
        self.existing = existing


class Papiq:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        delays: tuple[float, ...] = DELAYS,
    ) -> None:
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx2.Timeout(300.0, connect=10.0),
            transport=transport,
        )
        self._sleep = sleep
        self._delays = delays

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        upload: tuple[Path, str] | None = None,
    ) -> Any:
        """The decoded JSON answer (None for 204). Server errors and a dropped connection are
        repeated a few times; every other refusal is an `ApiError`."""
        for attempt in range(len(self._delays) + 1):
            try:
                if upload is None:
                    response = await self._client.request(
                        method, API + path, json=json, params=params
                    )
                else:
                    path_, filename = upload
                    with path_.open("rb") as handle:
                        response = await self._client.request(
                            method,
                            API + path,
                            data=data,
                            files={"file": (filename, handle, "application/octet-stream")},
                        )
            except httpx2.TransportError as error:
                if attempt == len(self._delays):
                    raise ApiError(0, f"Papiq is not reachable: {type(error).__name__}") from None
                await self._sleep(self._delays[attempt])
                continue
            if response.status_code in RETRY_STATUS and attempt < len(self._delays):
                await self._sleep(self._delays[attempt])
                continue
            if response.status_code == 429 and attempt < len(self._delays):
                await self._sleep(_wait(response.headers.get("retry-after")))
                continue
            if response.status_code >= 400:
                raise _error(response)
            return None if response.status_code == 204 else response.json()
        raise AssertionError("unreachable")

    async def _object(self, method: str, path: str, **arguments: Any) -> dict[str, Any]:
        return cast("dict[str, Any]", await self.request(method, path, **arguments))

    # --- reading -----------------------------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        return await self._object("GET", "/auth/me")

    async def items(self, path: str) -> list[dict[str, Any]]:
        return cast("list[dict[str, Any]]", await self.request("GET", path))

    async def documents(self, *, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"all_users": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        return await self._object("GET", "/documents", params=params)

    async def document(self, id: str) -> dict[str, Any]:
        return await self._object("GET", f"/documents/{id}")

    async def log(self, id: str) -> list[dict[str, Any]]:
        return cast("list[dict[str, Any]]", await self.request("GET", f"/documents/{id}/log"))

    # --- writing -----------------------------------------------------------------------------

    async def create_user(self, username: str) -> dict[str, Any]:
        return await self._object("POST", "/users", json={"username": username})

    async def set_active(self, id: str, active: bool) -> dict[str, Any]:
        return await self._object("PATCH", f"/users/{id}", json={"active": active})

    async def create_named(self, path: str, name: str) -> dict[str, Any]:
        return await self._object("POST", path, json={"name": name})

    async def create_attribute(
        self, name: str, data_type: str, choices: tuple[str, ...]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "data_type": data_type}
        if choices:
            body["choices"] = list(choices)
        return await self._object("POST", "/attributes", json=body)

    async def set_choices(self, id: str, choices: list[str]) -> dict[str, Any]:
        return await self._object("PATCH", f"/attributes/{id}", json={"choices": choices})

    async def create_drawer(self, name: str, owner_id: str) -> dict[str, Any]:
        return await self._object("POST", "/drawers", json={"name": name, "owner_id": owner_id})

    async def share(self, drawer_id: str, user_id: str, level: str) -> dict[str, Any]:
        return await self._object(
            "PUT", f"/drawers/{drawer_id}/shares/{user_id}", json={"level": level}
        )

    async def upload(self, path: Path, filename: str, fields: dict[str, str]) -> dict[str, Any]:
        return await self._object("POST", "/documents", data=fields, upload=(path, filename))


def _wait(header: str | None) -> float:
    """Seconds to wait for a `Retry-After` header; five if it is not a plain number."""
    try:
        return min(max(float(header or "5"), 0.0), 60.0)
    except ValueError:
        return 5.0


def _error(response: httpx2.Response) -> ApiError:
    detail, existing = response.text[:300], None
    try:
        body = response.json()
        detail = str(body.get("detail") or body.get("title") or detail)
        existing = body.get("existing_document_id")
    except (ValueError, AttributeError):
        pass
    return ApiError(response.status_code, detail, existing)

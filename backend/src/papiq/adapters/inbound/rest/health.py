"""`GET /health`: whether the API can reach its stores. No authentication, for health checks
of the container."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from papiq.adapters.inbound.rest.context import Context, HealthCheck
from papiq.adapters.inbound.rest.schemas import Health

log = logging.getLogger(__name__)

TIMEOUT = 5.0  # seconds per check

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health of the API",
    response_model=Health,
    responses={
        503: {"model": Health, "description": "The database or the object store is not reachable"}
    },
)
async def health(context: Context) -> JSONResponse:
    names = list(context.health_checks)
    results = await asyncio.gather(*(_run(name, context.health_checks[name]) for name in names))
    checks = dict(zip(names, results, strict=True))
    failed = {name for name, result in checks.items() if result == "failed"}
    status: Literal["ok", "degraded", "unavailable"] = "ok"
    if failed:
        status = "degraded" if failed <= context.optional_checks else "unavailable"
    body = Health(status=status, checks=checks)
    return JSONResponse(body.model_dump(), status_code=503 if status == "unavailable" else 200)


async def _run(name: str, check: HealthCheck) -> Literal["ok", "failed"]:
    try:
        await asyncio.wait_for(check(), timeout=TIMEOUT)
    except Exception:
        log.warning("health check failed", extra={"check": name}, exc_info=True)
        return "failed"
    return "ok"

"""Documents: upload, status, processing log, retry and reprocessing."""

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response

from papiq.adapters.inbound.rest.auth import CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    DocumentAccepted,
    DocumentStatus,
    LogEntry,
    ReprocessRequest,
)
from papiq.adapters.inbound.rest.upload import FILE_FIELD, read_upload
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DocumentId, DrawerId
from papiq.core.domain.pipeline import Step

router = APIRouter(prefix="/documents", tags=["documents"])

DRAWER_FIELD = "drawer_id"

_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "string",
                            "format": "binary",
                            "description": "PDF, JPEG, PNG or TIFF; recognised by content.",
                        },
                        DRAWER_FIELD: {
                            "type": "string",
                            "format": "uuid",
                            "description": "Target drawer; default: the owner's default drawer.",
                        },
                    },
                }
            }
        },
    }
}


@router.post(
    "",
    status_code=202,
    summary="Upload a document",
    description=(
        "Stores the file and starts processing in the background. The file is hashed while it "
        "is received; a file the caller already has is rejected with 409. Follow the progress "
        "at `status_url` or with `GET /events`."
    ),
    response_model=DocumentAccepted,
    responses=problem_responses(400, 401, 403, 404, 409, 413, 415, 422),
    openapi_extra=_UPLOAD_BODY,
)
async def upload(
    request: Request, response: Response, user: CurrentUser, context: Context
) -> DocumentAccepted:
    received = await read_upload(request, max_size=context.max_upload_size, fields=[DRAWER_FIELD])
    try:
        drawer = _drawer(received.fields.get(DRAWER_FIELD))
        document = await context.pipeline.receive(
            user, received.file, filename=received.filename, drawer=drawer
        )
    finally:
        await asyncio.shield(asyncio.to_thread(received.file.path.unlink, missing_ok=True))
    url = str(request.url_for("get_document", id=document.id).path)
    response.headers["Location"] = url
    return DocumentAccepted(id=document.id, status_url=url)


@router.get(
    "/{id}",
    name="get_document",
    summary="Status of a document",
    response_model=DocumentStatus,
    responses=problem_responses(401, 404, 422),
)
async def get_document(id: UUID, user: CurrentUser, context: Context) -> DocumentStatus:
    return DocumentStatus.of(await context.documents.get(user, DocumentId(id)))


@router.get(
    "/{id}/log",
    summary="Processing log of a document",
    description="Every execution of every step, oldest first.",
    response_model=list[LogEntry],
    responses=problem_responses(401, 404, 422),
)
async def processing_log(id: UUID, user: CurrentUser, context: Context) -> list[LogEntry]:
    return [
        LogEntry.of(run) for run in await context.documents.processing_log(user, DocumentId(id))
    ]


@router.post(
    "/{id}/retry",
    status_code=202,
    summary="Repeat the failed step",
    description="Owner only. Processing continues with the following steps.",
    response_model=DocumentStatus,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def retry(id: UUID, user: CurrentUser, context: Context) -> DocumentStatus:
    return DocumentStatus.of(await context.pipeline.retry(user, DocumentId(id)))


@router.post(
    "/{id}/reprocess",
    status_code=202,
    summary="Process again from a step",
    description=(
        "Owner only. Discards the results from `from_step` on, e.g. after a model change. Not "
        "while processing runs, and not past a failed step."
    ),
    response_model=DocumentStatus,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def reprocess(
    id: UUID, body: ReprocessRequest, user: CurrentUser, context: Context
) -> DocumentStatus:
    step = Step(body.from_step.value)
    return DocumentStatus.of(await context.pipeline.reprocess_from(user, DocumentId(id), step))


def _drawer(value: str | None) -> DrawerId | None:
    if value is None or not value.strip():
        return None
    try:
        return DrawerId(UUID(value.strip()))
    except ValueError:
        raise ValidationError(f"drawer_id: not a UUID: {value!r}") from None

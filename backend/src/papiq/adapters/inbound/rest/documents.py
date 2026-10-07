"""Documents: list, metadata, moving, deleting, files; upload, processing log, retry and
reprocessing; the inbox, review and confirmation.

Documents the caller may not read are "not found", the same as documents that do not exist:
the list leaves them out, filters and pages only ever see readable ones.
"""

import asyncio
import base64
import binascii
import os
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    ConfirmRequest,
    DocumentAccepted,
    DocumentDetails,
    DocumentPage,
    DocumentPatch,
    InboxItemOut,
    InboxPage,
    LogEntry,
    MoveRequest,
    ReprocessRequest,
    ReviewOut,
)
from papiq.adapters.inbound.rest.upload import FILE_FIELD, read_upload
from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.documents import UNSET, DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
)
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.ports import DocumentFilter
from papiq.core.services.documents import MAX_PAGE, DocumentFile

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=PROTECTED)
inbox = APIRouter(prefix="/inbox", tags=["inbox"], dependencies=PROTECTED)


class LaneFilter(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    PROCESSING = "processing"


_LANES: dict[LaneFilter, Lane | None] = {
    LaneFilter.GREEN: Lane.GREEN,
    LaneFilter.YELLOW: Lane.YELLOW,
    LaneFilter.RED: Lane.RED,
    LaneFilter.PROCESSING: None,
}

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
    responses={
        202: {
            "description": "Accepted; processing runs in the background.",
            "headers": {
                "Location": {
                    "description": "The status of the document.",
                    "schema": {"type": "string"},
                }
            },
            "content": {
                "application/json": {
                    "example": {
                        "id": "01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b",
                        "status_url": "/api/v1/documents/01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b",
                    }
                }
            },
        },
        **problem_responses(400, 401, 403, 404, 409, 413, 415, 422),
    },
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
    "",
    summary="List documents",
    description=(
        "The documents the caller may read, newest first: their own, and green documents in "
        "drawers they own or that are shared with them. Filters combine; `tag_id` and `lane` "
        "may repeat (all tags, any lane; `processing`: no lane yet). Full-text search follows "
        "with the search index."
    ),
    response_model=DocumentPage,
    responses=problem_responses(401, 422),
)
async def list_documents(
    user: CurrentUser,
    context: Context,
    contact_id: UUID | None = None,
    document_type_id: UUID | None = None,
    tag_id: Annotated[list[UUID] | None, Query(max_length=50)] = None,
    drawer_id: UUID | None = None,
    lane: Annotated[list[LaneFilter] | None, Query(max_length=4)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[str | None, Query(max_length=64, description="`next_cursor`.")] = None,
) -> DocumentPage:
    filter = DocumentFilter(
        contact=None if contact_id is None else ContactId(contact_id),
        document_type=None if document_type_id is None else DocumentTypeId(document_type_id),
        tags=frozenset(TagId(tag) for tag in tag_id or ()),
        drawer=None if drawer_id is None else DrawerId(drawer_id),
        lanes=None if not lane else frozenset(_LANES[item] for item in lane),
    )
    views = await context.documents.query(user, filter, before=_decode_cursor(cursor), limit=limit)
    next_cursor = _encode_cursor(views[-1].document.id) if len(views) == limit else None
    return DocumentPage(
        items=[DocumentDetails.of(view.document, view.access) for view in views],
        next_cursor=next_cursor,
    )


@router.get(
    "/{id}",
    name="get_document",
    summary="A document",
    description="Metadata, processing state and lane, and the caller's access.",
    response_model=DocumentDetails,
    responses=problem_responses(401, 404, 422),
)
async def get_document(id: UUID, user: CurrentUser, context: Context) -> DocumentDetails:
    view = await context.documents.view(user, DocumentId(id))
    return DocumentDetails.of(view.document, view.access)


@router.patch(
    "/{id}",
    summary="Change a document's metadata",
    description=(
        "Needs write access (owner or a `read_write` share). Referenced contact, type, tags "
        "and attributes must exist; attribute values must fit their type and apply to the "
        "document type."
    ),
    response_model=DocumentDetails,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def update_document(
    id: UUID, body: DocumentPatch, user: CurrentUser, context: Context
) -> DocumentDetails:
    changes = await _changes(body, user, context)
    document = await context.documents.update_metadata(user, DocumentId(id), changes)
    view = await context.documents.view(user, document.id)
    return DocumentDetails.of(view.document, view.access)


@router.post(
    "/{id}/move",
    status_code=204,
    summary="Move a document to another drawer",
    description=(
        "The owner moves into drawers they may write to; an admin moves any document into any "
        "drawer, without getting read access. Shares do not allow moving."
    ),
    responses=problem_responses(401, 403, 404, 422),
)
async def move_document(id: UUID, body: MoveRequest, user: CurrentUser, context: Context) -> None:
    await context.documents.move(user, DocumentId(id), DrawerId(body.drawer_id))


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a document",
    description="Owner only.",
    responses=problem_responses(401, 403, 404, 422),
)
async def delete_document(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.documents.delete(user, DocumentId(id))


def _file_route(file: DocumentFile, summary: str, media: str, disposition: str) -> None:
    @router.get(
        f"/{{id}}/{file.value}",
        name=f"get_document_{file.value}",
        summary=summary,
        description="Needs read access. 404 while the file does not exist (yet).",
        response_class=FileResponse,
        responses={
            200: {"content": {media: {"schema": {"type": "string", "format": "binary"}}}},
            **problem_responses(401, 404, 422),
        },
    )
    async def download(id: UUID, user: CurrentUser, context: Context) -> FileResponse:
        return await _download(context, user, DocumentId(id), file, disposition)


_file_route(DocumentFile.ORIGINAL, "The original file", "application/octet-stream", "attachment")
_file_route(
    DocumentFile.ARCHIVE, "The archive PDF (PDF/A with text layer)", "application/pdf", "inline"
)
_file_route(DocumentFile.PREVIEW, "The preview image of the first page", "image/webp", "inline")


async def _download(
    context: Context, user: UserId, id: DocumentId, file: DocumentFile, disposition: str
) -> FileResponse:
    descriptor, name = await asyncio.to_thread(tempfile.mkstemp, prefix="papiq-download-")
    os.close(descriptor)
    path = Path(name)
    try:
        info = await context.documents.download(user, id, file, path)
    except BaseException:
        await asyncio.shield(asyncio.to_thread(path.unlink, missing_ok=True))
        raise
    return FileResponse(
        path,
        media_type=info.media_type,
        filename=info.filename,
        content_disposition_type=disposition,
        headers={
            "X-Content-Type-Options": "nosniff",
            # A file shown in the browser runs no scripts and reaches nothing of this site.
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
        },
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get(
    "/{id}/log",
    summary="Processing log of a document",
    description="Owner only. Every execution of every step, oldest first.",
    response_model=list[LogEntry],
    responses=problem_responses(401, 403, 404, 422),
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
    response_model=DocumentDetails,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def retry(id: UUID, user: CurrentUser, context: Context) -> DocumentDetails:
    return _details(await context.pipeline.retry(user, DocumentId(id)))


@router.post(
    "/{id}/reprocess",
    status_code=202,
    summary="Process again from a step",
    description=(
        "Owner only. Discards the results from `from_step` on, e.g. after a model change. Not "
        "while processing runs, not past a failed step, and not from `file` while uncertain "
        "fields wait for confirmation."
    ),
    response_model=DocumentDetails,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def reprocess(
    id: UUID, body: ReprocessRequest, user: CurrentUser, context: Context
) -> DocumentDetails:
    step = Step(body.from_step.value)
    return _details(await context.pipeline.reprocess_from(user, DocumentId(id), step))


@router.get(
    "/{id}/review",
    summary="What the model proposed for a document",
    description=(
        "Owner only. The open steps and fields, and the latest classification and attribute "
        "extraction by the model: each field as proposed, checked and applied."
    ),
    response_model=ReviewOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def review(id: UUID, user: CurrentUser, context: Context) -> ReviewOut:
    return ReviewOut.of(await context.documents.review(user, DocumentId(id)))


@router.post(
    "/{id}/confirm",
    status_code=202,
    summary="Confirm a document from the inbox",
    description=(
        "Owner only, for yellow and red documents that are not being processed. Every open "
        "field of the steps before `resume_at` needs a decision: a value or null in `changes`, "
        "or its suggestion with `accept_suggestions`. Otherwise 422 lists the open fields in "
        "`open_fields`. The results before `resume_at` count as confirmed; processing continues "
        "from there up to filing."
    ),
    response_model=DocumentDetails,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def confirm(
    id: UUID, body: ConfirmRequest, user: CurrentUser, context: Context
) -> DocumentDetails:
    changes = await _changes(body.changes, user, context)
    document = await context.pipeline.confirm(
        user,
        DocumentId(id),
        changes,
        accept_suggestions=body.accept_suggestions,
        resume_at=Step(body.resume_at.value),
    )
    return _details(document)


@inbox.get(
    "",
    summary="The caller's inbox",
    description=(
        "The caller's yellow and red documents, newest first, each with its open steps and "
        "fields. Confirm them with `POST /documents/{id}/confirm`, or repeat a failed step with "
        "`retry`."
    ),
    response_model=InboxPage,
    responses=problem_responses(401, 422),
)
async def list_inbox(
    user: CurrentUser,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[str | None, Query(max_length=64, description="`next_cursor`.")] = None,
) -> InboxPage:
    items = await context.documents.inbox(user, before=_decode_cursor(cursor), limit=limit)
    next_cursor = _encode_cursor(items[-1].document.id) if len(items) == limit else None
    return InboxPage(items=[InboxItemOut.of(item) for item in items], next_cursor=next_cursor)


def _details(document: Any) -> DocumentDetails:
    """Retry and reprocessing are the owner's: full access."""
    return DocumentDetails.of(document, ShareLevel.READ_WRITE)


async def _changes(body: DocumentPatch, user: UserId, context: Context) -> DocumentChanges:
    given = body.model_fields_set
    attributes: dict[AttributeId, object] = {}
    if body.attributes:
        definitions = {a.id: a for a in await context.master_data.list_attributes(user)}
        for attribute_id, value in body.attributes.items():
            definition = definitions.get(AttributeId(attribute_id))
            attributes[AttributeId(attribute_id)] = (
                value if definition is None else _attribute_value(definition, value)
            )
    if "title" in given and body.title is None:
        raise ValidationError("title: must not be null")
    return DocumentChanges(
        title=body.title if body.title is not None else UNSET,
        contact_id=_given(given, "contact_id", body.contact_id, ContactId),
        document_type_id=_given(given, "document_type_id", body.document_type_id, DocumentTypeId),
        tag_ids=(
            frozenset(TagId(tag) for tag in body.tag_ids or ()) if "tag_ids" in given else UNSET
        ),
        document_date=body.document_date if "document_date" in given else UNSET,
        attributes=attributes,
    )


def _given[T](given: set[str], name: str, value: UUID | None, kind: type[T]) -> Any:
    if name not in given:
        return UNSET
    return None if value is None else kind(value)  # type: ignore[call-arg]


def _encode_cursor(id: DocumentId) -> str:
    return base64.urlsafe_b64encode(id.bytes).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> DocumentId | None:
    if not cursor:
        return None
    try:
        return DocumentId(UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))))
    except (ValueError, binascii.Error):
        raise ValidationError("cursor: invalid") from None


def _attribute_value(definition: AttributeDefinition, value: Any) -> object:
    """The JSON form of an attribute value as the domain type; None removes the value."""
    if value is None:
        return None
    invalid = ValidationError(
        f"attribute '{definition.name}' ({definition.data_type}) does not accept {value!r:.100}"
    )
    try:
        match definition.data_type:
            case AttributeType.NUMBER if isinstance(value, int | float | str) and not isinstance(
                value, bool
            ):
                return Decimal(str(value))
            case AttributeType.AMOUNT if isinstance(value, dict) and set(value) == {
                "amount",
                "currency",
            }:
                return Money(Decimal(str(value["amount"])), value["currency"])
            case AttributeType.DATE if isinstance(value, str):
                return date.fromisoformat(value)
            case AttributeType.LINK if isinstance(value, str):
                return Url(value)
    except (InvalidOperation, ValueError, TypeError):
        raise invalid from None
    return value  # text, choice, boolean: checked by the domain


def _drawer(value: str | None) -> DrawerId | None:
    if value is None or not value.strip():
        return None
    try:
        return DrawerId(UUID(value.strip()))
    except ValueError:
        raise ValidationError(f"drawer_id: not a UUID: {value!r}") from None

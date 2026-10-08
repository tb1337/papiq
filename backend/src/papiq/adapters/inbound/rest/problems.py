"""Errors as problem details (RFC 9457, `application/problem+json`).

Domain errors map to HTTP statuses here; every endpoint documents the problems it can return.
"""

import logging
import math
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from papiq.adapters.inbound.rest.upload import MalformedUploadError, UploadTooLargeError
from papiq.core.domain.errors import (
    AuthenticationError,
    ConflictError,
    DuplicateDocumentError,
    IdentityProviderError,
    InvalidTransitionError,
    NotFoundError,
    OpenFieldsError,
    PermissionDeniedError,
    SearchError,
    SecondFactorRequiredError,
    TooManyAttemptsError,
    UnsupportedMediaTypeError,
    ValidationError,
)

log = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class Problem(BaseModel):
    """What went wrong. `type` is `about:blank`: `title` and `status` say it all."""

    type: str = "about:blank"
    title: str = Field(examples=["Not Found"])
    status: int = Field(examples=[404])
    detail: str | None = Field(default=None, examples=["document 0199… not found"])
    existing_document_id: UUID | None = Field(
        default=None, description="For a duplicate: the document that has the same file."
    )
    open_fields: list[str] | None = Field(
        default=None, description="Confirming a document: the fields that need a decision."
    )
    second_factor_required: bool | None = Field(
        default=None,
        description="Sign-in: the password was right; send it again with `code` or "
        "`recovery_code`.",
    )


# status: (title, example detail)
_PROBLEMS: dict[int, tuple[str, str]] = {
    400: ("Bad Request", "expected multipart/form-data with a boundary"),
    401: ("Unauthorized", "authentication is required"),
    429: ("Too Many Requests", "too many failed attempts; try again later"),
    502: ("Bad Gateway", "the identity provider cannot be reached"),
    403: ("Forbidden", "only the owner controls processing of this document"),
    404: ("Not Found", "document 01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b not found"),
    405: ("Method Not Allowed", "the method is not allowed here"),
    409: ("Conflict", "duplicate of document 01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b"),
    413: ("Content Too Large", "the request body is larger than 1048576 bytes"),
    415: ("Unsupported Media Type", "unsupported file type"),
    422: ("Unprocessable Content", "from_step: Input should be 'ocr', 'parse', ..."),
    500: ("Internal Server Error", "an unexpected error occurred"),
    503: ("Service Unavailable", "database: unreachable"),
}


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for the given statuses and 500, with problem schema and example."""
    statuses = (*statuses, 500)
    return {
        status: {
            "description": _PROBLEMS[status][0],
            "content": {
                PROBLEM_JSON: {
                    "schema": {"$ref": "#/components/schemas/Problem"},
                    "example": {
                        "type": "about:blank",
                        "title": _PROBLEMS[status][0],
                        "status": status,
                        "detail": _PROBLEMS[status][1],
                    },
                }
            },
        }
        for status in statuses
    }


def problem(
    status: int,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body = Problem(title=_PROBLEMS.get(status, (str(status), ""))[0], status=status)
    body.detail = detail
    for name, value in extra.items():
        setattr(body, name, value)
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=dict(headers or {}),
    )


def install(app: FastAPI) -> None:
    """Register the handlers that turn errors into problems."""

    def status_of(error: Exception) -> int:
        for kind, status in _STATUSES:
            if isinstance(error, kind):
                return status
        raise error

    async def domain_error(request: Request, error: Exception) -> JSONResponse:
        if isinstance(error, DuplicateDocumentError):
            return problem(409, str(error), existing_document_id=error.existing)
        if isinstance(error, OpenFieldsError):
            return problem(422, str(error), open_fields=list(error.fields))
        if isinstance(error, SecondFactorRequiredError):
            return problem(401, str(error), second_factor_required=True)
        if isinstance(error, AuthenticationError):
            return problem(401, str(error), headers={"WWW-Authenticate": "Bearer"})
        if isinstance(error, TooManyAttemptsError):
            seconds = max(1, math.ceil(error.retry_after.total_seconds()))
            return problem(429, str(error), headers={"Retry-After": str(seconds)})
        return problem(status_of(error), str(error))

    async def validation_error(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'][1:]) or 'body'}: {item['msg']}"
            for item in error.errors()
        )
        return problem(422, details)

    async def http_error(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, HTTPException)
        return problem(error.status_code, str(error.detail), headers=error.headers)

    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # Details stay in the server log; the client learns only that something broke.
        log.error("request failed", exc_info=error)
        return problem(500, _PROBLEMS[500][1])

    for kind, _ in _STATUSES:
        app.add_exception_handler(kind, domain_error)
    app.add_exception_handler(Exception, unexpected_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)


# Most specific first.
_STATUSES: list[tuple[type[Exception], int]] = [
    (AuthenticationError, 401),
    (TooManyAttemptsError, 429),
    (IdentityProviderError, 502),
    (UnsupportedMediaTypeError, 415),
    (ValidationError, 422),
    (NotFoundError, 404),
    (PermissionDeniedError, 403),
    (InvalidTransitionError, 409),
    (ConflictError, 409),
    (UploadTooLargeError, 413),
    (MalformedUploadError, 400),
    (SearchError, 503),
]

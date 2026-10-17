"""Errors as problem details (RFC 9457, `application/problem+json`): the error format of the
inbound adapters over HTTP (REST API, MCP endpoint)."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

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
    space_id: UUID | None = Field(
        default=None,
        description="Filing or moving refused: the space the caller may not write to.",
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
PROBLEMS: dict[int, tuple[str, str]] = {
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
    422: ("Unprocessable Content", "from_node: Input should be 'ocr', 'parse', ..."),
    500: ("Internal Server Error", "an unexpected error occurred"),
    503: ("Service Unavailable", "database: unreachable"),
}


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for the given statuses and 500, with problem schema and example."""
    statuses = (*statuses, 500)
    return {
        status: {
            "description": PROBLEMS[status][0],
            "content": {
                PROBLEM_JSON: {
                    "schema": {"$ref": "#/components/schemas/Problem"},
                    "example": {
                        "type": "about:blank",
                        "title": PROBLEMS[status][0],
                        "status": status,
                        "detail": PROBLEMS[status][1],
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
    body = Problem(title=PROBLEMS.get(status, (str(status), ""))[0], status=status)
    body.detail = detail
    for name, value in extra.items():
        setattr(body, name, value)
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=dict(headers or {}),
    )

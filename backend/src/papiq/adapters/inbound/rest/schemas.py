"""Request and response bodies of the API."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from papiq.core.domain.attributes import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    Money,
    Url,
)
from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.identity import ApiToken, ExternalIdentity, LoginMethod, TokenScope
from papiq.core.domain.ids import UserId
from papiq.core.domain.master_data import MasterData
from papiq.core.domain.pipeline import (
    PIPELINE,
    Lane,
    Outcome,
    ProcessingStatus,
    Step,
    StepRun,
)
from papiq.core.domain.users import Role, User

ReprocessStep = StrEnum(  # type: ignore[misc]
    "ReprocessStep", {step.name: step.value for step in PIPELINE[1:]}
)
"""Steps processing can restart from; receiving cannot be repeated."""


class DocumentAccepted(BaseModel):
    id: UUID
    status_url: str = Field(description="Where to follow the processing.")


class Processing(BaseModel):
    status: ProcessingStatus = Field(
        description=(
            "`processing`: `current_step` is due; `review`: stopped before `current_step` until "
            "the uncertain fields are confirmed; `completed`; `failed` at `current_step`."
        )
    )
    current_step: Step | None
    run: int = Field(description="Grows with every retry or reprocessing.")
    outcomes: dict[Step, Outcome] = Field(description="Results of the current run.")


class MoneyValue(BaseModel):
    amount: str = Field(examples=["12.50"], description="Decimal number as text, exact.")
    currency: str = Field(examples=["EUR"], description="ISO 4217 code.")


AttributeJson = Annotated[
    str | bool | MoneyValue,
    Field(
        description=(
            "By data type: text, choice and link: string; number: decimal as string; date: "
            "`YYYY-MM-DD`; boolean: true/false; amount: `{amount, currency}`."
        )
    ),
]


def attribute_json(value: AttributeValue) -> str | bool | MoneyValue:
    match value:
        case bool() | str():
            return value
        case Money(amount=amount, currency=currency):
            return MoneyValue(amount=str(amount), currency=currency)
        case Decimal():
            return str(value)
        case Url(value=url):
            return url
        case _:  # date
            return value.isoformat()


class DocumentDetails(BaseModel):
    """A document's metadata and processing state, with the caller's access."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b",
                    "title": "Electricity bill March",
                    "original_filename": "scan_0042.pdf",
                    "media_type": "application/pdf",
                    "owner_id": "01999d5e-1111-7c1e-b6a3-2f4d5e6f7a8b",
                    "drawer_id": "01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b",
                    "access": "read_write",
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "document_type_id": None,
                    "tag_ids": [],
                    "document_date": "2026-03-31",
                    "attributes": {
                        "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b": {
                            "amount": "84.20",
                            "currency": "EUR",
                        }
                    },
                    "lane": "green",
                    "processing": {
                        "status": "completed",
                        "current_step": None,
                        "run": 1,
                        "outcomes": {"ocr": "ok", "classify": "ok"},
                    },
                    "created_at": "2026-04-02T08:15:00Z",
                    "updated_at": "2026-04-02T08:16:10Z",
                }
            ]
        }
    )

    id: UUID
    title: str
    original_filename: str
    media_type: str = Field(examples=["application/pdf"])
    owner_id: UUID
    drawer_id: UUID
    access: ShareLevel = Field(description="What the caller may do: read, or read and write.")
    contact_id: UUID | None
    document_type_id: UUID | None
    tag_ids: list[UUID]
    document_date: date | None
    attributes: dict[UUID, AttributeJson] = Field(description="Values by attribute id.")
    lane: Lane | None = Field(description="None while processing runs.")
    processing: Processing
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, document: Document, access: ShareLevel) -> "DocumentDetails":
        state = document.processing
        return cls(
            id=document.id,
            title=document.title,
            original_filename=document.original_filename,
            media_type=document.media_type,
            owner_id=document.owner_id,
            drawer_id=document.drawer_id,
            access=access,
            contact_id=document.contact_id,
            document_type_id=document.document_type_id,
            tag_ids=sorted(document.tag_ids),
            document_date=document.document_date,
            attributes={
                key: attribute_json(value) for key, value in sorted(document.attributes.items())
            },
            lane=document.lane,
            processing=Processing(
                status=state.status,
                current_step=state.current_step,
                run=state.run,
                outcomes=dict(state.outcomes),
            ),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentPage(BaseModel):
    items: list[DocumentDetails]
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next page; null on the last page."
    )


class DocumentPatch(BaseModel):
    """Fields left out stay as they are; null removes a value."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "Electricity bill March",
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "tag_ids": ["01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b"],
                    "document_date": "2026-03-31",
                    "attributes": {
                        "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b": {
                            "amount": "84.20",
                            "currency": "EUR",
                        }
                    },
                }
            ]
        },
    )

    title: str | None = Field(default=None, min_length=1, max_length=500)
    contact_id: UUID | None = None
    document_type_id: UUID | None = None
    tag_ids: list[UUID] | None = Field(default=None, max_length=500)
    document_date: date | None = None
    attributes: dict[UUID, Any] | None = Field(
        default=None,
        description="Values by attribute id, in the form of `attributes` above; null removes.",
    )


class MoveRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"drawer_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b"}]},
    )

    drawer_id: UUID


class LogEntry(BaseModel):
    """One execution of one step."""

    step: Step
    run: int
    outcome: Outcome
    reason: str | None
    confidence: float | None
    model_version: str | None = Field(examples=["ocrmypdf 17.13.0, tesseract 5.5.0"])
    input: dict[str, Any]
    output: dict[str, Any]
    pipeline_version: str
    started_at: datetime
    duration_ms: float

    @classmethod
    def of(cls, run: StepRun) -> "LogEntry":
        result = run.result
        return cls(
            step=run.step,
            run=run.run,
            outcome=result.outcome,
            reason=result.reason,
            confidence=result.confidence,
            model_version=result.model_version,
            input=dict(result.input),
            output=dict(result.output),
            pipeline_version=run.pipeline_version,
            started_at=run.started_at,
            duration_ms=run.duration.total_seconds() * 1000,
        )


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"from_step": "classify"}]})

    from_step: ReprocessStep = Field(
        description="Discard the results from this step on and process again from there."
    )


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "failed"]] = Field(
        examples=[{"database": "ok", "object_store": "ok"}]
    )


class EventMessage(BaseModel):
    """A domain event, thin: fetch the document for details. Fields beyond `type`, `id`,
    `occurred_at` and `document_id` depend on the type."""

    model_config = {"extra": "allow"}

    type: str = Field(examples=["document.step_completed"])
    id: UUID
    occurred_at: datetime
    document_id: UUID


# --- sign-in and account ------------------------------------------------------------------------

Password = Annotated[SecretStr, Field(min_length=1, max_length=1024)]
Code = Annotated[str, Field(min_length=1, max_length=64)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"username": "alice", "password": "********", "code": "123456"}]
        },
    )

    username: str = Field(min_length=1, max_length=150, examples=["alice"])
    password: Password = Field(examples=["********"])
    code: str | None = Field(
        default=None, max_length=16, description="TOTP code, if the account has TOTP."
    )
    recovery_code: str | None = Field(
        default=None, max_length=64, description="Instead of `code`: a recovery code."
    )


class UserOut(BaseModel):
    """A user. Others than admins see only `id` and `username`."""

    id: UUID
    username: str = Field(examples=["alice"])
    role: Role | None = None
    active: bool | None = None
    created_at: datetime | None = None

    @classmethod
    def of(cls, user: User, *, full: bool = True) -> "UserOut":
        if not full:
            return cls(id=user.id, username=user.username)
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            active=user.active,
            created_at=user.created_at,
        )


class LinkOut(BaseModel):
    """A link to an account at the identity provider."""

    issuer: str = Field(examples=["https://id.example.org/realms/home"])
    subject: str
    created_at: datetime

    @classmethod
    def of(cls, link: ExternalIdentity) -> "LinkOut":
        return cls(issuer=link.issuer, subject=link.subject, created_at=link.created_at)


class SessionOut(BaseModel):
    """A new session; its token is in the cookie only."""

    user: UserOut
    method: LoginMethod
    expires_at: datetime
    csrf_token: str = Field(
        description="Send as header `X-CSRF-Token` with every changing request.",
        examples=["3f5c0e…"],
    )


class Me(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user": {
                        "id": "01999d5e-1111-7c1e-b6a3-2f4d5e6f7a8b",
                        "username": "alice",
                        "role": "user",
                        "active": True,
                        "created_at": "2026-01-10T09:00:00Z",
                    },
                    "authenticated_with": "token",
                    "csrf_token": None,
                    "session_expires_at": None,
                    "token_scope": "read",
                    "has_password": True,
                    "totp_enabled": False,
                    "linked_accounts": [],
                }
            ]
        }
    )

    user: UserOut
    authenticated_with: Literal["session", "token"]
    csrf_token: str | None = Field(description="With a session: for `X-CSRF-Token`.")
    session_expires_at: datetime | None
    token_scope: TokenScope | None = Field(description="With an API token: its scope.")
    has_password: bool
    totp_enabled: bool
    linked_accounts: list[LinkOut]


class PasswordChange(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"current_password": "********", "new_password": "********", "revoke_tokens": False}
            ]
        },
    )

    current_password: Password
    new_password: Password = Field(description="12 to 256 characters, not the username.")
    revoke_tokens: bool = Field(
        default=False, description="Also revoke all API tokens. Other sessions end anyway."
    )


class SessionsEnded(BaseModel):
    ended: int


class TotpSetupOut(BaseModel):
    """Shown once. Confirm with a code to turn TOTP on."""

    secret: str = Field(examples=["JBSWY3DPEHPK3PXP"])
    otpauth_uri: str = Field(examples=["otpauth://totp/Papiq:alice?secret=JBSWY3DPEHPK3PXP"])


class CodeIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"code": "123456"}]},
    )

    code: Code = Field(description="A TOTP code; where noted, a recovery code works too.")


class RecoveryCodes(BaseModel):
    """Shown once; each works once instead of a TOTP code."""

    recovery_codes: list[str] = Field(examples=[["ABCD-EFGH-IJKL-MNOP"]])


class TokenCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"name": "scanner", "scope": "read_write", "expires_at": "2027-01-01T00:00:00Z"}
            ]
        },
    )

    name: str = Field(min_length=1, max_length=100, examples=["scanner"])
    scope: TokenScope
    expires_at: AwareDatetime | None = None


class TokenOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-6666-7c1e-b6a3-2f4d5e6f7a8b",
                    "name": "scanner",
                    "scope": "read_write",
                    "created_at": "2026-04-01T10:00:00Z",
                    "expires_at": "2027-01-01T00:00:00Z",
                    "last_used_at": "2026-04-02T07:30:00Z",
                }
            ]
        }
    )

    id: UUID
    name: str
    scope: TokenScope
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None

    @classmethod
    def of(cls, token: ApiToken) -> "TokenOut":
        return cls(
            id=token.id,
            name=token.name,
            scope=token.scope,
            created_at=token.created_at,
            expires_at=token.expires_at,
            last_used_at=token.last_used_at,
        )


class TokenCreated(TokenOut):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-6666-7c1e-b6a3-2f4d5e6f7a8b",
                    "name": "scanner",
                    "scope": "read_write",
                    "created_at": "2026-04-01T10:00:00Z",
                    "expires_at": "2027-01-01T00:00:00Z",
                    "last_used_at": None,
                    "token": "papiq_<token shown once>",
                }
            ]
        }
    )

    token: str = Field(
        description="The token itself; shown only now.", examples=["papiq_<token shown once>"]
    )


class OidcInfo(BaseModel):
    enabled: bool
    display_name: str | None = Field(examples=["Single sign-on"])


class AuthorizationUrl(BaseModel):
    authorization_url: str = Field(description="Send the browser there.")


class Removed(BaseModel):
    removed: int


# --- users --------------------------------------------------------------------------------------


class UserCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"username": "bob", "role": "user", "password": "********"}]
        },
    )

    username: str = Field(min_length=1, max_length=150)
    role: Role = Role.USER
    password: Password | None = Field(
        default=None, description="Optional; 12 to 256 characters, not the username."
    )


class UserPatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"role": "admin", "active": True}]},
    )

    role: Role | None = None
    active: bool | None = Field(
        default=None, description="false ends the user's sessions and blocks their tokens."
    )


class PasswordReset(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"password": "********", "revoke_tokens": True}]},
    )

    password: Password
    revoke_tokens: bool = False


class AccountOut(UserOut):
    has_password: bool
    totp_enabled: bool
    linked_accounts: list[LinkOut]


# --- master data --------------------------------------------------------------------------------

Name = Annotated[str, Field(min_length=1, max_length=200, examples=["ACME Energy"])]


class NameIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "ACME Energy"}]},
    )

    name: Name


class MasterDataOut(BaseModel):
    id: UUID
    name: str = Field(examples=["ACME Energy"])
    created_at: datetime

    @classmethod
    def of(cls, item: MasterData) -> "MasterDataOut":
        return cls(id=getattr(item, "id"), name=item.name, created_at=item.created_at)  # noqa: B009


class AttributeCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Amount",
                    "data_type": "amount",
                    "document_type_ids": ["01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b"],
                }
            ]
        },
    )

    name: Name
    data_type: AttributeType
    document_type_ids: list[UUID] | None = Field(
        default=None, description="null: global; otherwise only for these document types."
    )
    choices: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=200, description="For `choice` only."
    )


class AttributePatch(BaseModel):
    """Fields left out stay. The data type cannot change."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "Payment method", "choices": ["Card", "Transfer", "Cash"]}]
        },
    )

    name: Name | None = None
    choices: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None,
        max_length=200,
        description="All choices; removing one that documents use is a conflict (409).",
    )
    document_type_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "null: global. Narrowing is a conflict (409) while documents outside the new scope "
            "have values."
        ),
    )


class AttributeOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b",
                    "name": "Amount",
                    "data_type": "amount",
                    "document_type_ids": None,
                    "choices": [],
                    "created_at": "2026-01-10T09:00:00Z",
                }
            ]
        }
    )

    id: UUID
    name: str
    data_type: AttributeType
    document_type_ids: list[UUID] | None
    choices: list[str]
    created_at: datetime

    @classmethod
    def of(cls, item: AttributeDefinition) -> "AttributeOut":
        return cls(
            id=item.id,
            name=item.name,
            data_type=item.data_type,
            document_type_ids=(
                None if item.document_type_ids is None else sorted(item.document_type_ids)
            ),
            choices=list(item.choices),
            created_at=item.created_at,
        )


# --- drawers ------------------------------------------------------------------------------------


class ShareOut(BaseModel):
    user_id: UUID
    level: ShareLevel


class DrawerOut(BaseModel):
    id: UUID
    name: str = Field(examples=["Household"])
    owner_id: UUID
    is_default: bool
    access: ShareLevel = Field(description="The caller's access: owner (read_write) or share.")
    shares: list[ShareOut] | None = Field(description="Only for the owner.")
    created_at: datetime

    @classmethod
    def of(cls, drawer: Drawer, viewer: UserId) -> "DrawerOut":
        """As `viewer` sees it, who owns it or has a share."""
        owner = drawer.owner_id == viewer
        shares = sorted(drawer.shares.items())
        return cls(
            id=drawer.id,
            name=drawer.name,
            owner_id=drawer.owner_id,
            is_default=drawer.is_default,
            access=ShareLevel.READ_WRITE if owner else drawer.shares[viewer],
            shares=[ShareOut(user_id=u, level=level) for u, level in shares] if owner else None,
            created_at=drawer.created_at,
        )


class ShareIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"level": "read"}]},
    )

    level: ShareLevel

"""`/auth`: signing in and out, the caller's account, TOTP, API tokens, OpenID Connect."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from papiq.adapters.inbound.rest.auth import (
    PROTECTED,
    Authenticated,
    SessionPrincipal,
    clear_flow_cookie,
    clear_session_cookie,
    client_address,
    flow_cookie_name,
    optional_session,
    session_cookie_name,
    set_flow_cookie,
    set_session_cookie,
)
from papiq.adapters.inbound.rest.context import ApiContext, Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    AuthorizationUrl,
    CodeIn,
    LinkOut,
    LoginRequest,
    Me,
    OidcInfo,
    PasswordChange,
    RecoveryCodes,
    Removed,
    SessionOut,
    SessionsEnded,
    TokenCreate,
    TokenCreated,
    TokenOut,
    TotpSetupOut,
    UserOut,
)
from papiq.core.domain.errors import (
    AuthenticationError,
    NotFoundError,
    UnsupportedMediaTypeError,
)
from papiq.core.domain.identity import csrf_token
from papiq.core.domain.ids import ApiTokenId
from papiq.core.services.auth import Principal, SignedIn
from papiq.core.services.oidc import OidcService

public = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/auth", tags=["auth"], dependencies=PROTECTED)

SESSION_ONLY_NOTE = " Needs a session; API tokens are refused (403)."


def _session_out(signed_in: SignedIn) -> SessionOut:
    return SessionOut(
        user=UserOut.of(signed_in.user),
        method=signed_in.session.method,
        expires_at=signed_in.session.expires_at,
        csrf_token=signed_in.csrf_token,
    )


async def _json_body(request: Request) -> None:
    """Sign-in only as JSON: a form posted from another site cannot sign anyone in."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise UnsupportedMediaTypeError("send the sign-in as application/json")


# --- password sign-in ---------------------------------------------------------------------------


@public.post(
    "/login",
    summary="Sign in with username and password",
    description=(
        "Starts a session: the cookie is HTTP-only, `SameSite=Lax` and, in production, "
        "`Secure`. With TOTP on, a correct password without `code` (or `recovery_code`) "
        "answers 401 with `second_factor_required: true`. Failed attempts are counted per "
        "account and per address; too many answer 429 with `Retry-After`."
    ),
    response_model=SessionOut,
    responses={
        200: {"headers": {"Set-Cookie": {"description": "The session cookie."}}},
        **problem_responses(401, 415, 422, 429),
    },
    dependencies=[Depends(_json_body)],
)
async def login(
    body: LoginRequest, request: Request, response: Response, context: Context
) -> SessionOut:
    signed_in = await context.auth.login(
        body.username,
        body.password.get_secret_value(),
        code=body.code or None,
        recovery_code=body.recovery_code or None,
        source=client_address(request),
    )
    set_session_cookie(response, context, signed_in)
    return _session_out(signed_in)


@router.post(
    "/logout",
    status_code=204,
    summary="Sign out",
    description="Ends the session and removes its cookie." + SESSION_ONLY_NOTE,
    responses=problem_responses(401, 403),
)
async def logout(principal: SessionPrincipal, response: Response, context: Context) -> None:
    assert principal.session is not None
    await context.auth.logout(principal.session.id)
    clear_session_cookie(response, context)


@router.get(
    "/me",
    summary="The caller",
    description="Who is signed in and how; with a session also its CSRF token.",
    response_model=Me,
    responses=problem_responses(401),
)
async def me(principal: Authenticated, request: Request, context: Context) -> Me:
    account = await context.users.own_account(principal.id)
    session, token = principal.session, principal.api_token
    cookie = request.cookies.get(session_cookie_name(context)) if session else None
    return Me(
        user=UserOut.of(principal.user),
        authenticated_with="session" if session else "token",
        csrf_token=csrf_token(cookie) if cookie else None,
        session_expires_at=session.expires_at if session else None,
        token_scope=token.scope if token else None,
        has_password=account.has_password,
        totp_enabled=account.totp_enabled,
        linked_accounts=[LinkOut.of(link) for link in account.external_identities],
    )


@router.post(
    "/password",
    summary="Change the own password",
    description=(
        "Ends all sessions and starts a new one for the caller (new cookie and CSRF token). "
        "API tokens stay unless `revoke_tokens`." + SESSION_ONLY_NOTE
    ),
    response_model=SessionOut,
    responses=problem_responses(401, 403, 422, 429),
)
async def change_password(
    body: PasswordChange, principal: SessionPrincipal, response: Response, context: Context
) -> SessionOut:
    assert principal.session is not None
    signed_in = await context.auth.change_password(
        principal.id,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
        session=principal.session.id,
        revoke_tokens=body.revoke_tokens,
    )
    assert signed_in is not None
    set_session_cookie(response, context, signed_in)
    return _session_out(signed_in)


@router.delete(
    "/sessions",
    summary="End all other sessions",
    description="Signs out every other browser of the caller." + SESSION_ONLY_NOTE,
    response_model=SessionsEnded,
    responses=problem_responses(401, 403),
)
async def end_other_sessions(principal: SessionPrincipal, context: Context) -> SessionsEnded:
    assert principal.session is not None
    ended = await context.auth.end_other_sessions(principal.id, keep=principal.session.id)
    return SessionsEnded(ended=ended)


# --- TOTP ---------------------------------------------------------------------------------------


@router.post(
    "/totp",
    status_code=201,
    summary="Start setting up TOTP",
    description=(
        "A new secret for the authenticator app, shown only now. TOTP is on once a code "
        "confirms it (`POST /auth/totp/confirm`)." + SESSION_ONLY_NOTE
    ),
    response_model=TotpSetupOut,
    responses=problem_responses(401, 403, 409),
)
async def begin_totp(principal: SessionPrincipal, context: Context) -> TotpSetupOut:
    setup = await context.auth.begin_totp(principal.id)
    return TotpSetupOut(secret=setup.secret, otpauth_uri=setup.uri)


@router.post(
    "/totp/confirm",
    summary="Turn TOTP on",
    description="With a code from the authenticator; returns recovery codes, shown only now."
    + SESSION_ONLY_NOTE,
    response_model=RecoveryCodes,
    responses=problem_responses(401, 403, 409, 422),
)
async def confirm_totp(
    body: CodeIn, principal: SessionPrincipal, context: Context
) -> RecoveryCodes:
    return RecoveryCodes(recovery_codes=await context.auth.confirm_totp(principal.id, body.code))


@router.post(
    "/totp/disable",
    status_code=204,
    summary="Turn TOTP off",
    description="Needs a current code or a recovery code." + SESSION_ONLY_NOTE,
    responses=problem_responses(401, 403, 409, 422, 429),
)
async def disable_totp(body: CodeIn, principal: SessionPrincipal, context: Context) -> None:
    await context.auth.disable_totp(principal.id, body.code)


@router.post(
    "/totp/recovery-codes",
    summary="New recovery codes",
    description="Replaces the old ones; needs a current code or a recovery code."
    + SESSION_ONLY_NOTE,
    response_model=RecoveryCodes,
    responses=problem_responses(401, 403, 409, 422, 429),
)
async def renew_recovery_codes(
    body: CodeIn, principal: SessionPrincipal, context: Context
) -> RecoveryCodes:
    codes = await context.auth.renew_recovery_codes(principal.id, body.code)
    return RecoveryCodes(recovery_codes=codes)


# --- API tokens ---------------------------------------------------------------------------------


@router.get(
    "/tokens",
    summary="The caller's API tokens",
    description="Without the tokens themselves." + SESSION_ONLY_NOTE,
    response_model=list[TokenOut],
    responses=problem_responses(401, 403),
)
async def list_tokens(principal: SessionPrincipal, context: Context) -> list[TokenOut]:
    return [TokenOut.of(token) for token in await context.auth.list_api_tokens(principal.id)]


@router.post(
    "/tokens",
    status_code=201,
    summary="Create an API token",
    description=(
        "`read` allows reading only; `read_write` everything the caller may do, except "
        "managing the own sign-in. The token is shown only now." + SESSION_ONLY_NOTE
    ),
    response_model=TokenCreated,
    responses=problem_responses(401, 403, 422),
)
async def create_token(
    body: TokenCreate, principal: SessionPrincipal, context: Context
) -> TokenCreated:
    token, value = await context.auth.create_api_token(
        principal.id, body.name, body.scope, expires_at=body.expires_at
    )
    return TokenCreated(**TokenOut.of(token).model_dump(), token=value)


@router.delete(
    "/tokens/{id}",
    status_code=204,
    summary="Revoke an API token",
    description=SESSION_ONLY_NOTE.strip(),
    responses=problem_responses(401, 403, 404, 422),
)
async def revoke_token(id: UUID, principal: SessionPrincipal, context: Context) -> None:
    await context.auth.revoke_api_token(principal.id, ApiTokenId(id))


# --- OpenID Connect -----------------------------------------------------------------------------


def _oidc(context: ApiContext) -> OidcService:
    if context.oidc is None:
        raise NotFoundError("identity provider", "oidc")
    return context.oidc


Next = Annotated[
    str | None,
    Query(max_length=2000, description="Where to go afterwards: a path on this site."),
]


@public.get(
    "/oidc",
    summary="Sign-in through an identity provider",
    description="Whether OpenID Connect is configured, and the provider's name.",
    response_model=OidcInfo,
    responses=problem_responses(),
)
async def oidc_info(context: Context) -> OidcInfo:
    oidc = context.oidc
    return OidcInfo(enabled=oidc is not None, display_name=oidc.display_name if oidc else None)


@public.get(
    "/oidc/login",
    status_code=302,
    summary="Sign in through the identity provider",
    description="Redirects the browser to the provider (Authorization Code Flow with PKCE).",
    response_class=RedirectResponse,
    responses={
        302: {"description": "To the identity provider; sets a short-lived cookie."},
        **problem_responses(404, 422, 502),
    },
)
async def oidc_login(context: Context, next: Next = None) -> RedirectResponse:
    start = await _oidc(context).begin(next)
    response = RedirectResponse(start.url, status_code=302)
    set_flow_cookie(response, context, start.flow)
    return response


@public.get(
    "/oidc/callback",
    status_code=303,
    summary="Return from the identity provider",
    description=(
        "The provider sends the browser here. Signs in (new session cookie; a session the "
        "browser had ends) or completes a link, then redirects to the path given at the start."
    ),
    response_class=RedirectResponse,
    responses={
        303: {"description": "To the path given at the start."},
        **problem_responses(401, 404, 409, 422, 502),
    },
)
async def oidc_callback(
    request: Request,
    context: Context,
    caller: Annotated[Principal | None, Depends(optional_session)],
    code: Annotated[str | None, Query(max_length=2000)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    oidc = _oidc(context)
    if error is not None or code is None or state is None:
        raise AuthenticationError("the identity provider did not sign you in")
    outcome = await oidc.complete(
        request.cookies.get(flow_cookie_name(context)),
        state=state,
        code=code,
        caller=None if caller is None else caller.id,
        current_session=None if caller is None or caller.session is None else caller.session.id,
    )
    response = RedirectResponse(outcome.redirect_to, status_code=303)
    clear_flow_cookie(response, context)
    if outcome.signed_in is not None:
        set_session_cookie(response, context, outcome.signed_in)
    return response


@router.post(
    "/oidc/link",
    summary="Link the own account to the identity provider",
    description=(
        "Returns where to send the browser and sets a short-lived cookie; the callback links "
        "the provider's account to the caller, who must still be signed in there."
        + SESSION_ONLY_NOTE
    ),
    response_model=AuthorizationUrl,
    responses=problem_responses(401, 403, 404, 422, 502),
)
async def oidc_link(
    principal: SessionPrincipal, response: Response, context: Context, next: Next = None
) -> AuthorizationUrl:
    start = await _oidc(context).begin_link(principal.id, next)
    set_flow_cookie(response, context, start.flow)
    return AuthorizationUrl(authorization_url=start.url)


@router.delete(
    "/oidc/link",
    summary="Remove the own links to the identity provider",
    description="Only with a password: without one, the account could not sign in."
    + SESSION_ONLY_NOTE,
    response_model=Removed,
    responses=problem_responses(401, 403, 404, 409),
)
async def oidc_unlink(principal: SessionPrincipal, context: Context) -> Removed:
    return Removed(removed=await _oidc(context).unlink(principal.id))

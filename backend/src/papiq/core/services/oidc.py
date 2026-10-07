"""Signing in through an OpenID Connect provider, and linking local accounts to it.

The flow (Authorization Code with PKCE):

1. `begin` creates state, nonce and PKCE verifier and seals them, with where to go afterwards
   and, for linking, the user, into an encrypted value for a short-lived cookie. The browser
   goes to the provider.
2. `complete`, at the callback, opens the sealed value (only the browser that began holds it,
   which binds the flow to that browser), compares the state, and lets the provider adapter
   exchange the code and check the ID token.
3. The account is found by issuer and subject, never by e-mail address. Without a link, an
   account is created only if configured (`auto_create`); otherwise the sign-in is refused.
   Linking needs the user who began it to be signed in at the callback.

Signing in through the provider asks for no local TOTP code: the provider handles that.
"""

import base64
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from papiq.core.domain.errors import AuthenticationError, ConflictError
from papiq.core.domain.identity import ExternalIdentity, LoginMethod, safe_redirect
from papiq.core.domain.ids import UserId
from papiq.core.domain.users import Role
from papiq.core.ports import (
    Clock,
    DecryptionError,
    OidcProvider,
    SecretCipher,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor
from papiq.core.services.auth import AuthService, SignedIn
from papiq.core.services.users import add_user

log = logging.getLogger(__name__)

FLOW_LIFETIME = timedelta(minutes=10)
_FLOW_CONTEXT = b"papiq-oidc-flow"


@dataclass(frozen=True)
class OidcStart:
    """`url`: where to send the browser; `flow`: the sealed flow, for an HTTP-only cookie."""

    url: str
    flow: str


@dataclass(frozen=True)
class OidcOutcome:
    """After the callback: a new session (sign-in) or none (an account was linked), and where
    to send the browser."""

    signed_in: SignedIn | None
    redirect_to: str


@dataclass(frozen=True)
class _Flow:
    state: str
    nonce: str
    code_verifier: str
    redirect_to: str
    link_user: UserId | None
    started_at: datetime


class OidcService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        auth: AuthService,
        provider: OidcProvider,
        cipher: SecretCipher,
        *,
        display_name: str,
        auto_create: bool = False,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._auth = auth
        self._provider = provider
        self._cipher = cipher
        self.display_name = display_name
        self.auto_create = auto_create

    async def begin(self, redirect_to: str | None = None) -> OidcStart:
        """Start signing in; afterwards the browser goes to `redirect_to` (a path here)."""
        return await self._begin(_new_flow(safe_redirect(redirect_to), None, self._clock.now()))

    async def begin_link(self, actor: UserId, redirect_to: str | None = None) -> OidcStart:
        """Start linking the caller's account to an account at the provider."""
        async with self._uow() as uow:
            await load_actor(uow, actor)
        return await self._begin(_new_flow(safe_redirect(redirect_to), actor, self._clock.now()))

    async def complete(
        self, sealed_flow: str | None, *, state: str, code: str, caller: UserId | None = None
    ) -> OidcOutcome:
        """Finish at the callback. `caller` is the user signed in on this browser, if any; it
        must be the user who began a link. AuthenticationError if the flow is missing, expired
        or does not match, the provider refuses, or no account may sign in; ConflictError if
        the provider's account is linked to another user."""
        flow = self._open(sealed_flow)
        if not hmac.compare_digest(flow.state, state):
            raise AuthenticationError("the sign-in does not match the one started here")
        identity = await self._provider.authenticate(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce
        )
        now = self._clock.now()

        if flow.link_user is not None:
            if caller != flow.link_user:
                raise AuthenticationError("sign in again to link the account")
            async with self._uow() as uow:
                await load_actor(uow, caller)
                existing = await uow.external_identities.find(identity.issuer, identity.subject)
                if existing is not None and existing.user_id != caller:
                    raise ConflictError("this account is linked to another user")
                if existing is None:
                    await uow.external_identities.add(
                        ExternalIdentity.link(
                            issuer=identity.issuer,
                            subject=identity.subject,
                            user_id=caller,
                            now=now,
                        )
                    )
                    await uow.commit()
            log.info("identity provider linked", extra={"user_id": str(caller)})
            return OidcOutcome(signed_in=None, redirect_to=flow.redirect_to)

        async with self._uow() as uow:
            link = await uow.external_identities.find(identity.issuer, identity.subject)
            if link is not None:
                user_id = link.user_id
            elif self.auto_create and identity.username:
                user = await add_user(uow, identity.username, Role.USER, now)
                await uow.external_identities.add(
                    ExternalIdentity.link(
                        issuer=identity.issuer, subject=identity.subject, user_id=user.id, now=now
                    )
                )
                await uow.commit()
                user_id = user.id
                log.info("user created at first sign-in", extra={"user_id": str(user.id)})
            else:
                raise AuthenticationError("no account here is linked to this sign-in")
        signed_in = await self._auth.sign_in(user_id, LoginMethod.OIDC)
        return OidcOutcome(signed_in=signed_in, redirect_to=flow.redirect_to)

    async def unlink(self, actor: UserId) -> int:
        """Remove the caller's links to the provider. ConflictError if the caller has no
        password: they could not sign in any more."""
        async with self._uow() as uow:
            await load_actor(uow, actor)
            credential = await uow.credentials.find(actor)
            if credential is None or credential.password_hash is None:
                raise ConflictError("set a password first; without it you could not sign in")
            removed = await uow.external_identities.remove_for_user(actor)
            await uow.commit()
        return removed

    async def links(self, actor: UserId) -> list[ExternalIdentity]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.external_identities.list_for(actor)

    async def _begin(self, flow: _Flow) -> OidcStart:
        url = await self._provider.authorization_url(
            state=flow.state, nonce=flow.nonce, code_verifier=flow.code_verifier
        )
        return OidcStart(url=url, flow=self._seal(flow))

    def _seal(self, flow: _Flow) -> str:
        data = json.dumps(
            {
                "state": flow.state,
                "nonce": flow.nonce,
                "verifier": flow.code_verifier,
                "redirect_to": flow.redirect_to,
                "link_user": None if flow.link_user is None else str(flow.link_user),
                "started_at": flow.started_at.isoformat(),
            }
        ).encode()
        sealed = self._cipher.encrypt(data, context=_FLOW_CONTEXT)
        return base64.urlsafe_b64encode(sealed).decode().rstrip("=")

    def _open(self, sealed: str | None) -> _Flow:
        expired = AuthenticationError("the sign-in has expired or was not started here")
        if not sealed:
            raise expired
        try:
            raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
            data = json.loads(self._cipher.decrypt(raw, context=_FLOW_CONTEXT))
            flow = _Flow(
                state=data["state"],
                nonce=data["nonce"],
                code_verifier=data["verifier"],
                redirect_to=safe_redirect(data["redirect_to"]),
                link_user=None if data["link_user"] is None else UserId(_uuid(data["link_user"])),
                started_at=datetime.fromisoformat(data["started_at"]),
            )
        except (DecryptionError, ValueError, KeyError, TypeError):
            raise expired from None
        if not (flow.started_at <= self._clock.now() < flow.started_at + FLOW_LIFETIME):
            raise expired
        return flow


def _new_flow(redirect_to: str, link_user: UserId | None, now: datetime) -> _Flow:
    return _Flow(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(48),  # 64 characters, RFC 7636: 43 to 128
        redirect_to=redirect_to,
        link_user=link_user,
        started_at=now,
    )


def _uuid(value: str) -> UUID:
    return UUID(value)

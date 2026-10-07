"""Signing in and the caller's own account: password, TOTP, sessions and API tokens.

Sign-in (`login`):
- Unknown and deactivated users are checked against a dummy hash, so all failures take the
  same time and give the same answer.
- Failures are counted per account (also for unknown names) and per source address; while
  either is blocked, attempts are refused with TooManyAttemptsError before anything is checked.
- With TOTP on, a correct password without a code raises SecondFactorRequiredError; the caller
  sends password and code (or a recovery code) again. A wrong code counts as a failure.
- Success starts a new session (never reuses one) and clears the account's failures. A password
  hash made with older parameters is replaced.

Sessions end after the idle time or the maximum age, at sign-out, and when the password
changes (except the caller's own, which is renewed). API tokens survive a password change
unless the caller asks to revoke them. Deactivated users are rejected however they come.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from papiq.core.domain.errors import (
    AuthenticationError,
    ConcurrencyError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    SecondFactorRequiredError,
    TooManyAttemptsError,
    ValidationError,
)
from papiq.core.domain.identity import (
    ACCOUNT_THROTTLE,
    SOURCE_THROTTLE,
    ApiToken,
    Credential,
    LoginFailures,
    LoginMethod,
    Session,
    ThrottleRule,
    TokenScope,
    TotpSetting,
    account_key,
    check_new_password,
    csrf_token,
    hash_token,
    new_recovery_codes,
    normalize_password,
    normalize_recovery_code,
    source_key,
)
from papiq.core.domain.ids import ApiTokenId, SessionId, UserId
from papiq.core.domain.users import User
from papiq.core.ports import (
    Clock,
    PasswordHasher,
    SecretCipher,
    Totp,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor

log = logging.getLogger(__name__)

TOTP_ISSUER = "Papiq"
_RECORD_ATTEMPTS = 3


@dataclass(frozen=True)
class SessionPolicy:
    idle: timedelta = timedelta(days=1)
    max_age: timedelta = timedelta(days=30)
    # Last use is written at most this often, not on every request.
    touch_interval: timedelta = timedelta(minutes=1)


@dataclass(frozen=True)
class SignedIn:
    """A new session. `token` goes to the client (cookie) and is not stored."""

    user: User
    session: Session
    token: str

    @property
    def csrf_token(self) -> str:
        return csrf_token(self.token)


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: through a session or an API token."""

    user: User
    session: Session | None = None
    api_token: ApiToken | None = None

    @property
    def id(self) -> UserId:
        return self.user.id

    @property
    def can_write(self) -> bool:
        return self.api_token is None or self.api_token.scope.can_write


@dataclass(frozen=True)
class TotpSetup:
    """Shown once while setting up TOTP: the secret for manual entry and as `otpauth://` URI."""

    secret: str
    uri: str


class AuthService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        hasher: PasswordHasher,
        cipher: SecretCipher,
        totp: Totp,
        sessions: SessionPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher
        self._cipher = cipher
        self._totp = totp
        self._policy = sessions or SessionPolicy()
        self._dummy_hash: str | None = None

    @property
    def session_policy(self) -> SessionPolicy:
        return self._policy

    # --- sign-in --------------------------------------------------------------------------------

    async def login(
        self,
        username: str,
        password: str,
        *,
        code: str | None = None,
        recovery_code: str | None = None,
        source: str | None = None,
    ) -> SignedIn:
        """Sign in with password and, if the account has TOTP, a code or a recovery code."""
        now = self._clock.now()
        throttles = _throttles(username, source)
        await self._check_throttles(throttles, now)

        async with self._uow() as uow:
            user = await uow.users.find_by_username(username)
            credential = None if user is None else await uow.credentials.find(user.id)
        stored = None if credential is None else credential.password_hash
        valid = await self._hasher.verify(
            stored or await self._dummy(), normalize_password(password)
        )
        if user is None or credential is None or stored is None or not valid or not user.active:
            await self._fail(throttles, now)
            raise AuthenticationError("invalid username or password")

        if credential.totp_enabled and not code and not recovery_code:
            raise SecondFactorRequiredError("a one-time code is required")
        new_hash = None
        if self._hasher.needs_rehash(stored):
            new_hash = await self._hasher.hash(normalize_password(password))
        return await self._complete_login(
            user.id, throttles, now, code=code, recovery_code=recovery_code, new_hash=new_hash
        )

    async def _complete_login(
        self,
        user_id: UserId,
        throttles: Sequence[tuple[str, ThrottleRule]],
        now: datetime,
        *,
        code: str | None,
        recovery_code: str | None,
        new_hash: str | None,
    ) -> SignedIn:
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            credential = await uow.credentials.get(user_id)
            valid = not credential.totp_enabled or self._second_factor(
                credential, code, recovery_code, now
            )
            if valid:
                if new_hash is not None:
                    credential.password_hash = new_hash
                await uow.credentials.update(credential)
                signed_in = await self._start_session(uow, user, LoginMethod.PASSWORD, now)
                await uow.login_failures.remove(throttles[0][0])
                await uow.commit()
        if not valid:
            await self._fail(throttles, now)
            raise AuthenticationError("invalid one-time code")
        log.info("signed in", extra={"user_id": str(user.id), "method": "password"})
        return signed_in

    async def sign_in(self, user_id: UserId, method: LoginMethod) -> SignedIn:
        """Start a session for a user authenticated elsewhere (identity provider)."""
        now = self._clock.now()
        async with self._uow() as uow:
            user = await uow.users.find(user_id)
            if user is None or not user.active:
                raise AuthenticationError("the account is not active")
            signed_in = await self._start_session(uow, user, method, now)
            await uow.commit()
        log.info("signed in", extra={"user_id": str(user.id), "method": method.value})
        return signed_in

    async def logout(self, session: SessionId) -> None:
        async with self._uow() as uow:
            await uow.sessions.remove(session)
            await uow.commit()

    # --- authenticating requests ----------------------------------------------------------------

    async def authenticate_session(self, token: str) -> Principal:
        """The caller behind a session token; AuthenticationError if it is unknown, expired,
        idle for too long, or the user is deactivated."""
        now = self._clock.now()
        async with self._uow() as uow:
            session = await uow.sessions.find_by_token(hash_token(token))
            if session is None:
                raise AuthenticationError("authentication is required")
            user = await uow.users.find(session.user_id)
            if not session.is_valid(now, self._policy.idle):
                await uow.sessions.remove(session.id)
                await uow.commit()
                raise AuthenticationError("the session has expired")
            if user is None or not user.active:
                raise AuthenticationError("authentication is required")
            if now - session.last_seen_at >= self._policy.touch_interval:
                await uow.sessions.touch(session.id, now)
                await _commit_quietly(uow)
        return Principal(user=user, session=session)

    async def authenticate_token(self, token: str) -> Principal:
        """The caller behind an API token; AuthenticationError if it is unknown, expired,
        revoked, or the user is deactivated."""
        now = self._clock.now()
        async with self._uow() as uow:
            api_token = await uow.api_tokens.find_by_token(hash_token(token))
            if api_token is None or not api_token.is_valid(now):
                raise AuthenticationError("the token is invalid or has expired")
            user = await uow.users.find(api_token.user_id)
            if user is None or not user.active:
                raise AuthenticationError("the token is invalid or has expired")
            last = api_token.last_used_at
            if last is None or now - last >= self._policy.touch_interval:
                await uow.api_tokens.touch(api_token.id, now)
                await _commit_quietly(uow)
        return Principal(user=user, api_token=api_token)

    # --- own password ---------------------------------------------------------------------------

    async def change_password(
        self,
        actor: UserId,
        current: str,
        new: str,
        *,
        session: SessionId | None = None,
        revoke_tokens: bool = False,
    ) -> SignedIn | None:
        """Change the caller's password. All sessions end; if the caller uses `session`, it is
        replaced by a new one, which is returned. API tokens stay unless `revoke_tokens`."""
        now = self._clock.now()
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            credential = await uow.credentials.find(actor)
        throttles = _throttles(user.username, None)
        await self._check_throttles(throttles, now)
        stored = None if credential is None else credential.password_hash
        if stored is None or not await self._hasher.verify(stored, normalize_password(current)):
            await self._fail(throttles, now)
            raise PermissionDeniedError("the current password is wrong")
        new_hash = await self._hasher.hash(check_new_password(new, user.username))

        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            credential = await uow.credentials.get(actor)
            if credential.password_hash != stored:
                raise ConcurrencyError("the password was changed meanwhile")
            credential.password_hash = new_hash
            credential.password_changed_at = now
            await uow.credentials.update(credential)
            await uow.sessions.remove_for_user(actor)
            if revoke_tokens:
                await uow.api_tokens.remove_for_user(actor)
            signed_in = None
            if session is not None:
                signed_in = await self._start_session(uow, user, LoginMethod.PASSWORD, now)
            await uow.commit()
        log.info("password changed", extra={"user_id": str(actor), "tokens_revoked": revoke_tokens})
        return signed_in

    async def end_other_sessions(self, actor: UserId, *, keep: SessionId | None) -> int:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            ended = await uow.sessions.remove_for_user(actor, keep=keep)
            await uow.commit()
        return ended

    # --- TOTP -----------------------------------------------------------------------------------

    async def begin_totp(self, actor: UserId) -> TotpSetup:
        """Start setting up TOTP with a new secret; it takes effect when a code confirms it.
        Replaces a setup that was not confirmed. ConflictError if TOTP is on already."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            credential = await _credential(uow, actor)
            if credential.totp_enabled:
                raise ConflictError("TOTP is already on; turn it off first")
            secret = self._totp.new_secret()
            credential.totp = TotpSetting(secret=self._encrypt(actor, secret))
            await uow.credentials.update(credential)
            await uow.commit()
        uri = self._totp.provisioning_uri(secret, account=user.username, issuer=TOTP_ISSUER)
        return TotpSetup(secret=secret, uri=uri)

    async def confirm_totp(self, actor: UserId, code: str) -> list[str]:
        """Turn TOTP on with a code from the authenticator; returns new recovery codes, shown
        only now."""
        now = self._clock.now()
        async with self._uow() as uow:
            await load_actor(uow, actor)
            credential = await _credential(uow, actor)
            totp = credential.totp
            if totp is None or totp.confirmed:
                raise ConflictError("no TOTP setup to confirm")
            step = self._totp.matching_step(self._decrypt(credential), code, now)
            if step is None:
                raise ValidationError("the code is not valid")
            totp.confirmed, totp.last_step = True, step
            codes = new_recovery_codes()
            credential.recovery_codes = {hash_token(normalize_recovery_code(c)) for c in codes}
            await uow.credentials.update(credential)
            await uow.commit()
        log.info("TOTP turned on", extra={"user_id": str(actor)})
        return codes

    async def disable_totp(self, actor: UserId, code: str) -> None:
        """Turn TOTP off; needs a current code or a recovery code."""
        await self._with_second_factor(actor, code, _turn_off_totp)
        log.info("TOTP turned off", extra={"user_id": str(actor)})

    async def renew_recovery_codes(self, actor: UserId, code: str) -> list[str]:
        """New recovery codes in place of the old ones; needs a current code or a recovery
        code."""
        codes = new_recovery_codes()

        def renew(credential: Credential) -> None:
            credential.recovery_codes = {hash_token(normalize_recovery_code(c)) for c in codes}

        await self._with_second_factor(actor, code, renew)
        return codes

    async def _with_second_factor(
        self, actor: UserId, code: str, change: Callable[[Credential], None]
    ) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
        throttles = _throttles(user.username, None)
        await self._check_throttles(throttles, now)
        async with self._uow() as uow:
            await load_actor(uow, actor)
            credential = await _credential(uow, actor)
            if not credential.totp_enabled:
                raise ConflictError("TOTP is off")
            valid = self._second_factor(credential, code, code, now)
            if valid:
                change(credential)
                await uow.credentials.update(credential)
                await uow.commit()
        if not valid:
            await self._fail(throttles, now)
            raise PermissionDeniedError("the code is not valid")

    # --- API tokens -----------------------------------------------------------------------------

    async def create_api_token(
        self,
        actor: UserId,
        name: str,
        scope: TokenScope,
        *,
        expires_at: datetime | None = None,
    ) -> tuple[ApiToken, str]:
        """A new token; the second value is the token itself, shown only now."""
        now = self._clock.now()
        async with self._uow() as uow:
            await load_actor(uow, actor)
            api_token, token = ApiToken.issue(
                user_id=actor, name=name, scope=scope, now=now, expires_at=expires_at
            )
            await uow.api_tokens.add(api_token)
            await uow.commit()
        log.info(
            "API token created",
            extra={"user_id": str(actor), "token_id": str(api_token.id), "scope": scope.value},
        )
        return api_token, token

    async def list_api_tokens(self, actor: UserId) -> list[ApiToken]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.api_tokens.list_for(actor)

    async def revoke_api_token(self, actor: UserId, id: ApiTokenId) -> None:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            api_token = await uow.api_tokens.find(id)
            if api_token is None or api_token.user_id != actor:
                raise NotFoundError("API token", id)
            await uow.api_tokens.remove(id)
            await uow.commit()
        log.info("API token revoked", extra={"user_id": str(actor), "token_id": str(id)})

    # --- helpers --------------------------------------------------------------------------------

    async def _start_session(
        self, uow: UnitOfWork, user: User, method: LoginMethod, now: datetime
    ) -> SignedIn:
        session, token = Session.start(
            user_id=user.id, method=method, now=now, max_age=self._policy.max_age
        )
        await uow.sessions.add(session)
        return SignedIn(user=user, session=session, token=token)

    def _second_factor(
        self, credential: Credential, code: str | None, recovery_code: str | None, now: datetime
    ) -> bool:
        if code:
            step = self._totp.matching_step(self._decrypt(credential), code, now)
            if step is not None and credential.accept_totp_step(step):
                return True
        return bool(recovery_code) and credential.use_recovery_code(recovery_code or "")

    def _encrypt(self, user: UserId, secret: str) -> bytes:
        return self._cipher.encrypt(secret.encode(), context=user.bytes)

    def _decrypt(self, credential: Credential) -> str:
        assert credential.totp is not None
        return self._cipher.decrypt(
            credential.totp.secret, context=credential.user_id.bytes
        ).decode()

    async def _dummy(self) -> str:
        """A hash with the current parameters to verify against when there is no user."""
        if self._dummy_hash is None:
            self._dummy_hash = await self._hasher.hash(secrets.token_urlsafe(16))
        return self._dummy_hash

    async def _check_throttles(
        self, throttles: Sequence[tuple[str, ThrottleRule]], now: datetime
    ) -> None:
        async with self._uow() as uow:
            for key, _ in throttles:
                failures = await uow.login_failures.find(key)
                wait = None if failures is None else failures.retry_after(now)
                if wait is not None:
                    raise TooManyAttemptsError(wait)

    async def _fail(self, throttles: Sequence[tuple[str, ThrottleRule]], now: datetime) -> None:
        """Count a failure under every key. Concurrent failures may conflict; then counting is
        tried again, and at worst one failure goes uncounted."""
        for key, rule in throttles:
            for attempt in range(_RECORD_ATTEMPTS):
                try:
                    async with self._uow() as uow:
                        failures = await uow.login_failures.find(key)
                        if failures is None:
                            failures = LoginFailures.first(key, now)
                            failures.record(rule, now)
                            await uow.login_failures.add(failures)
                        else:
                            failures.record(rule, now)
                            await uow.login_failures.update(failures)
                        await uow.commit()
                    break
                except (ConcurrencyError, ConflictError):
                    if attempt + 1 == _RECORD_ATTEMPTS:
                        log.warning("could not count a failed sign-in", extra={"key": key})
                    await asyncio.sleep(0)
        log.info("sign-in failed", extra={"keys": [key for key, _ in throttles]})


def _turn_off_totp(credential: Credential) -> None:
    credential.totp = None
    credential.recovery_codes = set()


def _throttles(username: str, source: str | None) -> list[tuple[str, ThrottleRule]]:
    """The account first, then the source."""
    throttles = [(account_key(username), ACCOUNT_THROTTLE)]
    if source:
        throttles.append((source_key(source), SOURCE_THROTTLE))
    return throttles


async def _credential(uow: UnitOfWork, user: UserId) -> Credential:
    credential = await uow.credentials.find(user)
    if credential is None:
        credential = Credential(user_id=user, password_hash=None)
        await uow.credentials.add(credential)
    return credential


async def _commit_quietly(uow: UnitOfWork) -> None:
    """Commit a usage timestamp; losing it to a concurrent change does no harm."""
    try:
        await uow.commit()
    except (ConcurrencyError, ConflictError):
        log.debug("usage timestamp not stored")

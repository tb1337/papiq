"""Identity repositories on SQL tables: credentials, sessions, API tokens, links to identity
providers and failed sign-ins. Updates are optimistic as in `repositories`; `touch` updates
only a timestamp and leaves the version alone."""

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, Table, and_, delete, insert, or_, select, update

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.identity import (
    ApiToken,
    Credential,
    ExternalIdentity,
    LoginFailures,
    LoginMethod,
    Session,
    TokenScope,
    TotpSetting,
)
from papiq.core.domain.ids import ApiTokenId, ExternalIdentityId, SessionId, UserId


async def _versioned_update(
    tx: Transaction,
    table: Table,
    kind: str,
    key: tuple[str, Any],
    version: int,
    values: dict[str, Any],
) -> None:
    """`UPDATE ... WHERE <key> AND version = :version`, as in `repositories.SqlRepository`."""
    column, id = table.c[key[0]], key[1]
    result = await tx.write(
        update(table)
        .where(column == id, table.c.version == version)
        .values({**values, "version": version + 1})
    )
    if result.rowcount == 0:
        exists = await tx.read(select(column).where(column == id))
        if exists.first() is None:
            raise NotFoundError(kind, str(id))
        raise ConcurrencyError(f"{kind} {id} was changed concurrently")


class SqlCredentialRepository:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def get(self, user: UserId) -> Credential:
        credential = await self.find(user)
        if credential is None:
            raise NotFoundError("credential", user)
        return credential

    async def find(self, user: UserId) -> Credential | None:
        table = t.credentials
        row = (await self._tx.read(select(table).where(table.c.user_id == user))).first()
        if row is None:
            return None
        codes = t.recovery_codes
        hashes = await self._tx.read(select(codes.c.code_hash).where(codes.c.user_id == user))
        return Credential(
            user_id=UserId(row.user_id),
            password_hash=row.password_hash,
            password_changed_at=row.password_changed_at,
            totp=(
                None
                if row.totp_secret is None
                else TotpSetting(
                    secret=bytes(row.totp_secret),
                    confirmed=row.totp_confirmed,
                    last_step=row.totp_last_step,
                )
            ),
            recovery_codes={code for (code,) in hashes},
            version=row.version,
        )

    async def add(self, credential: Credential) -> None:
        await self._tx.write(
            insert(t.credentials).values(
                **_credential_values(credential), version=credential.version
            )
        )
        await self._write_codes(credential)

    async def update(self, credential: Credential) -> None:
        await _versioned_update(
            self._tx,
            t.credentials,
            "credential",
            ("user_id", credential.user_id),
            credential.version,
            _credential_values(credential),
        )
        credential.version += 1
        codes = t.recovery_codes
        await self._tx.write(delete(codes).where(codes.c.user_id == credential.user_id))
        await self._write_codes(credential)

    async def remove(self, user: UserId) -> None:
        await self._tx.write(delete(t.credentials).where(t.credentials.c.user_id == user))

    async def _write_codes(self, credential: Credential) -> None:
        if credential.recovery_codes:
            await self._tx.write(
                insert(t.recovery_codes),
                [
                    {"user_id": credential.user_id, "code_hash": code}
                    for code in sorted(credential.recovery_codes)
                ],
            )


def _credential_values(credential: Credential) -> dict[str, Any]:
    totp = credential.totp
    return {
        "user_id": credential.user_id,
        "password_hash": credential.password_hash,
        "password_changed_at": credential.password_changed_at,
        "totp_secret": None if totp is None else totp.secret,
        "totp_confirmed": totp is not None and totp.confirmed,
        "totp_last_step": None if totp is None else totp.last_step,
    }


class SqlSessionRepository:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def add(self, session: Session) -> None:
        await self._tx.write(
            insert(t.sessions).values(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                method=session.method.value,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
                expires_at=session.expires_at,
                version=session.version,
            )
        )

    async def find_by_token(self, token_hash: str) -> Session | None:
        table = t.sessions
        row = (await self._tx.read(select(table).where(table.c.token_hash == token_hash))).first()
        return None if row is None else _session(row)

    async def touch(self, id: SessionId, last_seen_at: datetime) -> None:
        table = t.sessions
        await self._tx.write(
            update(table)
            .where(table.c.id == id, table.c.last_seen_at < last_seen_at)
            .values(last_seen_at=last_seen_at)
        )

    async def remove(self, id: SessionId) -> None:
        await self._tx.write(delete(t.sessions).where(t.sessions.c.id == id))

    async def remove_for_user(self, user: UserId, *, keep: SessionId | None = None) -> int:
        table = t.sessions
        statement = delete(table).where(table.c.user_id == user)
        if keep is not None:
            statement = statement.where(table.c.id != keep)
        return (await self._tx.write(statement)).rowcount

    async def purge(self, *, now: datetime, idle_before: datetime) -> int:
        table = t.sessions
        statement = delete(table).where(
            or_(table.c.expires_at <= now, table.c.last_seen_at <= idle_before)
        )
        return (await self._tx.write(statement)).rowcount


def _session(row: Row[Any]) -> Session:
    return Session(
        id=SessionId(row.id),
        user_id=UserId(row.user_id),
        token_hash=row.token_hash,
        method=LoginMethod(row.method),
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        version=row.version,
    )


class SqlApiTokenRepository:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def add(self, token: ApiToken) -> None:
        await self._tx.write(
            insert(t.api_tokens).values(
                id=token.id,
                user_id=token.user_id,
                name=token.name,
                scope=token.scope.value,
                token_hash=token.token_hash,
                created_at=token.created_at,
                expires_at=token.expires_at,
                last_used_at=token.last_used_at,
                version=token.version,
            )
        )

    async def find(self, id: ApiTokenId) -> ApiToken | None:
        return await self._first(t.api_tokens.c.id == id)

    async def find_by_token(self, token_hash: str) -> ApiToken | None:
        return await self._first(t.api_tokens.c.token_hash == token_hash)

    async def list_for(self, user: UserId) -> list[ApiToken]:
        table = t.api_tokens
        rows = await self._tx.read(
            select(table).where(table.c.user_id == user).order_by(table.c.created_at, table.c.id)
        )
        return [_api_token(row) for row in rows]

    async def touch(self, id: ApiTokenId, last_used_at: datetime) -> None:
        table = t.api_tokens
        await self._tx.write(
            update(table)
            .where(
                table.c.id == id,
                or_(table.c.last_used_at.is_(None), table.c.last_used_at < last_used_at),
            )
            .values(last_used_at=last_used_at)
        )

    async def remove(self, id: ApiTokenId) -> None:
        await self._tx.write(delete(t.api_tokens).where(t.api_tokens.c.id == id))

    async def remove_for_user(self, user: UserId) -> int:
        table = t.api_tokens
        return (await self._tx.write(delete(table).where(table.c.user_id == user))).rowcount

    async def _first(self, where: ColumnElement[bool]) -> ApiToken | None:
        row = (await self._tx.read(select(t.api_tokens).where(where))).first()
        return None if row is None else _api_token(row)


def _api_token(row: Row[Any]) -> ApiToken:
    return ApiToken(
        id=ApiTokenId(row.id),
        user_id=UserId(row.user_id),
        name=row.name,
        scope=TokenScope(row.scope),
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        version=row.version,
    )


class SqlExternalIdentityRepository:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def add(self, identity: ExternalIdentity) -> None:
        await self._tx.write(
            insert(t.external_identities).values(
                id=identity.id,
                issuer=identity.issuer,
                subject=identity.subject,
                user_id=identity.user_id,
                created_at=identity.created_at,
                version=identity.version,
            )
        )

    async def find(self, issuer: str, subject: str) -> ExternalIdentity | None:
        table = t.external_identities
        row = (
            await self._tx.read(
                select(table).where(and_(table.c.issuer == issuer, table.c.subject == subject))
            )
        ).first()
        return None if row is None else _external_identity(row)

    async def list_for(self, user: UserId) -> list[ExternalIdentity]:
        table = t.external_identities
        rows = await self._tx.read(
            select(table).where(table.c.user_id == user).order_by(table.c.created_at, table.c.id)
        )
        return [_external_identity(row) for row in rows]

    async def remove(self, id: ExternalIdentityId) -> None:
        table = t.external_identities
        await self._tx.write(delete(table).where(table.c.id == id))

    async def remove_for_user(self, user: UserId) -> int:
        table = t.external_identities
        return (await self._tx.write(delete(table).where(table.c.user_id == user))).rowcount


def _external_identity(row: Row[Any]) -> ExternalIdentity:
    return ExternalIdentity(
        id=ExternalIdentityId(row.id),
        issuer=row.issuer,
        subject=row.subject,
        user_id=UserId(row.user_id),
        created_at=row.created_at,
        version=row.version,
    )


class SqlLoginFailureRepository:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def find(self, key: str) -> LoginFailures | None:
        table = t.login_failures
        row = (await self._tx.read(select(table).where(table.c.key == key))).first()
        if row is None:
            return None
        return LoginFailures(
            key=row.key,
            failures=row.failures,
            first_failure_at=row.first_failure_at,
            blocked_until=row.blocked_until,
            version=row.version,
        )

    async def add(self, failures: LoginFailures) -> None:
        await self._tx.write(
            insert(t.login_failures).values(**_failure_values(failures), version=failures.version)
        )

    async def update(self, failures: LoginFailures) -> None:
        await _versioned_update(
            self._tx,
            t.login_failures,
            "login failures",
            ("key", failures.key),
            failures.version,
            _failure_values(failures),
        )
        failures.version += 1

    async def remove(self, key: str) -> None:
        await self._tx.write(delete(t.login_failures).where(t.login_failures.c.key == key))

    async def purge(self, *, before: datetime) -> int:
        table = t.login_failures
        statement = delete(table).where(
            table.c.first_failure_at < before,
            or_(table.c.blocked_until.is_(None), table.c.blocked_until < before),
        )
        return (await self._tx.write(statement)).rowcount


def _failure_values(failures: LoginFailures) -> dict[str, Any]:
    return {
        "key": failures.key,
        "failures": failures.failures,
        "first_failure_at": failures.first_failure_at,
        "blocked_until": failures.blocked_until,
    }

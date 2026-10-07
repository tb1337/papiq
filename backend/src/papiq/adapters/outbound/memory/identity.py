"""In-memory identity repositories, on the tables of the in-memory unit of work."""

from datetime import datetime, timedelta
from uuid import UUID

from papiq.adapters.outbound.memory.rows import MemoryRepository
from papiq.core.domain.identity import (
    ApiToken,
    Credential,
    ExternalIdentity,
    LoginFailures,
    Session,
    ThrottleRule,
    failure_id,
)
from papiq.core.domain.ids import ApiTokenId, ExternalIdentityId, SessionId, UserId


class MemoryCredentialRepository(MemoryRepository[UserId, Credential]):
    async def remove(self, user: UserId) -> None:
        self._remove_rows([row for row in self._all() if row.user_id == user])


class MemorySessionRepository(MemoryRepository[SessionId, Session]):
    async def find_by_token(self, token_hash: str) -> Session | None:
        return next((self._copy(row) for row in self._all() if row.token_hash == token_hash), None)

    async def touch(self, id: SessionId, last_seen_at: datetime) -> None:
        self._touch(id, "last_seen_at", last_seen_at)

    async def remove(self, id: SessionId) -> None:
        self._remove_rows([row for row in self._all() if row.id == id])

    async def remove_for_user(self, user: UserId, *, keep: SessionId | None = None) -> int:
        return self._remove_rows(
            [row for row in self._all() if row.user_id == user and row.id != keep]
        )

    async def purge(self, *, now: datetime, idle_before: datetime) -> int:
        return self._remove_rows(
            [row for row in self._all() if row.expires_at <= now or row.last_seen_at <= idle_before]
        )


class MemoryApiTokenRepository(MemoryRepository[ApiTokenId, ApiToken]):
    async def find_by_token(self, token_hash: str) -> ApiToken | None:
        return next((self._copy(row) for row in self._all() if row.token_hash == token_hash), None)

    async def list_for(self, user: UserId) -> list[ApiToken]:
        rows = [row for row in self._all() if row.user_id == user]
        return [self._copy(row) for row in sorted(rows, key=lambda row: (row.created_at, row.id))]

    async def touch(self, id: ApiTokenId, last_used_at: datetime) -> None:
        self._touch(id, "last_used_at", last_used_at)

    async def remove(self, id: ApiTokenId) -> None:
        self._remove_rows([row for row in self._all() if row.id == id])

    async def remove_for_user(self, user: UserId) -> int:
        return self._remove_rows([row for row in self._all() if row.user_id == user])


class MemoryExternalIdentityRepository(MemoryRepository[ExternalIdentityId, ExternalIdentity]):
    async def find(  # type: ignore[override]
        self, issuer: str, subject: str
    ) -> ExternalIdentity | None:
        return next(
            (
                self._copy(row)
                for row in self._all()
                if row.issuer == issuer and row.subject == subject
            ),
            None,
        )

    async def list_for(self, user: UserId) -> list[ExternalIdentity]:
        rows = [row for row in self._all() if row.user_id == user]
        return [self._copy(row) for row in sorted(rows, key=lambda row: (row.created_at, row.id))]

    async def remove(self, id: ExternalIdentityId) -> None:
        self._remove_rows([row for row in self._all() if row.id == id])

    async def remove_for_user(self, user: UserId) -> int:
        return self._remove_rows([row for row in self._all() if row.user_id == user])


class MemoryLoginFailureRepository(MemoryRepository[UUID, LoginFailures]):
    """Atomic, since nothing in a call waits for anything else."""

    async def find(self, key: str) -> LoginFailures | None:  # type: ignore[override]
        return await super().find(failure_id(key))

    async def reserve(self, key: str, rule: ThrottleRule, now: datetime) -> timedelta | None:
        failures = await self.find(key)
        if failures is None:
            failures = LoginFailures.first(key, now)
            wait = failures.reserve(rule, now)
            await self.add(failures)
            return wait
        wait = failures.reserve(rule, now)
        if wait is None:
            await self.update(failures)
        return wait

    async def release(self, key: str, rule: ThrottleRule) -> None:
        failures = await self.find(key)
        if failures is not None:
            failures.release(rule)
            await self.update(failures)

    async def remove(self, key: str) -> None:
        self._remove_rows([row for row in self._all() if row.key == key])

    async def purge(self, *, before: datetime) -> int:
        return self._remove_rows(
            [
                row
                for row in self._all()
                if row.first_failure_at < before
                and (row.blocked_until is None or row.blocked_until < before)
            ]
        )

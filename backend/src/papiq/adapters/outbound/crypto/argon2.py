"""Argon2id password hashes.

The default parameters are the RFC 9106 low-memory profile (3 passes, 64 MiB, 4 lanes). A hash
records its parameters: when they change here, older hashes still verify and `needs_rehash`
reports them, so they are replaced at the next sign-in. Hashing runs in a worker thread; at most
`max_concurrency` hashes run at once, which bounds the memory a burst of sign-ins can take.
"""

import asyncio

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2PasswordHasher:
    def __init__(
        self,
        *,
        time_cost: int = profiles.RFC_9106_LOW_MEMORY.time_cost,
        memory_cost: int = profiles.RFC_9106_LOW_MEMORY.memory_cost,
        parallelism: int = profiles.RFC_9106_LOW_MEMORY.parallelism,
        max_concurrency: int = 4,
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism
        )
        self._slots = asyncio.Semaphore(max_concurrency)

    async def hash(self, password: str) -> str:
        async with self._slots:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, hash: str, password: str) -> bool:
        async with self._slots:
            return await asyncio.to_thread(self._verify, hash, password)

    def needs_rehash(self, hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(hash)
        except InvalidHashError:
            return True

    def _verify(self, hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(hash, password)
        except (VerificationError, InvalidHashError):
            return False

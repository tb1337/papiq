"""Errors raised by the core. Inbound adapters map them to their protocol (e.g. HTTP status)."""

from uuid import UUID


class DomainError(Exception):
    """Base class for all errors of the core."""


class NotFoundError(DomainError):
    """The entity does not exist, or the caller may not know that it exists."""

    def __init__(self, kind: str, id: UUID | str) -> None:
        super().__init__(f"{kind} {id} not found")
        self.kind = kind
        self.id = id


class PermissionDeniedError(DomainError):
    """The caller may see the entity but not perform the action."""


class ValidationError(DomainError):
    """A value or a change breaks a rule of the domain model."""


class InvalidTransitionError(DomainError):
    """The processing state does not allow the requested step or action."""


class ConflictError(DomainError):
    """The change collides with existing data, e.g. a name that is already taken."""


class DuplicateDocumentError(ConflictError):
    """The owner already has a document with the same content."""

    def __init__(self, existing: UUID) -> None:
        super().__init__(f"duplicate of document {existing}")
        self.existing = existing


class ConcurrencyError(ConflictError):
    """The entity was changed by someone else since it was read (stale version)."""

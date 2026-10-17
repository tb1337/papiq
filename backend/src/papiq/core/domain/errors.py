"""Errors raised by the core. Inbound adapters map them to their protocol (e.g. HTTP status)."""

from datetime import timedelta
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


class SpaceWriteDeniedError(PermissionDeniedError):
    """Filing into a space or moving a document there needs write access to it; `space_id`
    names the space, so a caller can tell which of its choices was refused."""

    def __init__(self, space_id: object, label: str) -> None:
        super().__init__(f"no write access to space {label}")
        self.space_id = space_id


class ValidationError(DomainError):
    """A value or a change breaks a rule of the domain model."""


class OpenFieldsError(ValidationError):
    """Confirming a document needs a decision on each of its open fields."""

    def __init__(self, fields: tuple[str, ...]) -> None:
        super().__init__(f"decide the open fields first: {', '.join(fields)}")
        self.fields = fields


class InvalidTransitionError(DomainError):
    """The processing state does not allow the requested node or action."""


class ConflictError(DomainError):
    """The change collides with existing data, e.g. a name that is already taken."""


class DuplicateDocumentError(ConflictError):
    """The owner already has a document with the same content."""

    def __init__(self, existing: UUID) -> None:
        super().__init__(f"duplicate of document {existing}")
        self.existing = existing


class ConcurrencyError(ConflictError):
    """The entity was changed by someone else since it was read (stale version)."""


class UnprocessableDocumentError(DomainError):
    """The file cannot be processed, e.g. because it is damaged or encrypted. Repeating the
    node would not help."""


class UnsupportedMediaTypeError(ValidationError):
    """The file type is not supported."""


class ImageError(ValidationError):
    """An image of a supported type cannot be read: it is damaged, or it would be too large
    once decoded (a decompression bomb)."""


class LogoFetchError(DomainError):
    """No logo could be fetched from a website: none it declares fits, or the site cannot be
    reached safely (an address that is not public, no HTTPS, too slow, too large)."""


class LogoFetchDisabledError(DomainError):
    """Papiq does not fetch logos (`PAPIQ_LOGO_FETCH` is off)."""


class AuthenticationError(DomainError):
    """The caller could not be authenticated: wrong or missing credentials, an expired or
    revoked session or token, a deactivated account. The message never says which."""


class SecondFactorRequiredError(AuthenticationError):
    """The password was right; the account needs a TOTP or recovery code as well."""


class TooManyAttemptsError(DomainError):
    """Too many failed sign-ins; further attempts are refused for `retry_after`."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("too many failed attempts; try again later")
        self.retry_after = retry_after


class IdentityProviderError(DomainError):
    """The identity provider could not be reached or answered with something unusable."""


class LanguageModelError(DomainError):
    """The language model could not be reached or answered with an error (HTTP status, time
    out, unusable response). Usually temporary, so the node is retried."""


class EmbeddingsError(DomainError):
    """The embedding model could not be reached or answered with an error."""


class SearchError(DomainError):
    """The search index failed."""


class SearchUnavailableError(SearchError):
    """The search index is not configured, cannot be reached or does not answer in time. Usually
    temporary, so index jobs are repeated."""


class SearchIndexError(SearchError):
    """The search index refused a request or a task failed (e.g. vectors of the wrong length)."""


class ClassifierError(DomainError):
    """The classifier cannot learn from the examples (too few, no text) or cannot read a model
    (damaged, or written in another format)."""


class ClassifierUnavailableError(DomainError):
    """No classifier is configured (`PAPIQ_CLASSIFIER=none`)."""


class PatternTimeoutError(DomainError):
    """A regular expression of a rule took longer than its time limit."""


class MissingValueError(DomainError):
    """A template writes a value the document does not have (`path`, e.g. `field.la_brutto`).
    What the template was for is left as it is; `{% if %}` or `| default` make a part
    optional."""

    def __init__(self, path: str) -> None:
        super().__init__(f"{path} has no value")
        self.path = path

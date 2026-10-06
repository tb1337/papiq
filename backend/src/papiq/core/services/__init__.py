"""Use cases. Every public method runs in one unit of work and checks the caller's rights.

Callers are identified by user id. A document or drawer the caller may not see is reported as
NotFoundError, so its existence is not revealed; PermissionDeniedError means the caller sees it
but may not perform the action.
"""

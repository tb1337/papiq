"""Where the API lives, for the app and for answers that link to it."""

PREFIX = "/api/v1"


def logo_url(contact: object, version: int) -> str:
    """The logo of a contact (`GET /contacts/{id}/logo`), with its version: a new logo is a new
    URL, so a browser may keep one as long as it likes."""
    return f"{PREFIX}/contacts/{contact}/logo?v={version}"

"""`python -m papiq.composition.openapi [FILE]`: write the API's OpenAPI document as JSON to FILE
(default: standard output), without configuration and without a server.

The web UI generates its TypeScript client from it (`web/openapi.json`); a unit test fails while
that file differs from the document of the code.
"""

import json
import sys
from pathlib import Path

from papiq.adapters.inbound.rest import ApiContext, create_app
from papiq.composition.container import build_memory_container, build_services


def openapi_document() -> dict[str, object]:
    """The OpenAPI document of the REST API. It does not depend on the configuration: the app is
    built on in-memory adapters only to read its routes."""
    container = build_memory_container()
    services = build_services(container)
    app = create_app(
        ApiContext(
            auth=services.auth,
            users=services.users,
            drawers=services.drawers,
            master_data=services.master_data,
            pipeline=services.pipeline,
            documents=services.documents,
            rules=services.rules,
            rule_applications=services.rule_applications,
            webhooks=services.webhooks,
            webhook_delivery=services.webhook_delivery,
            event_bus=container.event_bus,
            health_checks={},
            max_upload_size=1,
        )
    )
    return app.openapi()


def render(document: dict[str, object]) -> str:
    """The document as the checked-in file holds it."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: python -m papiq.composition.openapi [FILE]", file=sys.stderr)
        return 2
    text = render(openapi_document())
    if argv:
        Path(argv[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

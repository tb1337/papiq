"""`python -m papiq.composition`: validate the configuration and print it without secrets.

Exits with status 1 and a readable message when the configuration is invalid.
"""

import sys

import structlog

from papiq.composition.errors import ConfigurationError
from papiq.composition.logging_setup import configure_logging
from papiq.composition.settings import find_unknown_variables, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1

    configure_logging(settings)
    log = structlog.get_logger("papiq.composition")
    for variable in find_unknown_variables():
        log.warning("unknown configuration variable", variable=variable)
    log.info("configuration valid", **settings.describe())
    return 0


if __name__ == "__main__":
    sys.exit(main())

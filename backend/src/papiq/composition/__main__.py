"""`python -m papiq.composition [check | migrate]`.

- `check` (default): validate the configuration and print it without secrets.
- `migrate`: validate, then bring the configured database to the newest schema. Run before the
  API and the worker start (in the image: `init-migrations`).

Exits with status 1 and a readable message when the configuration is invalid or the migration
fails.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog

from papiq.composition.database import migrate_database
from papiq.composition.errors import ConfigurationError
from papiq.composition.logging_setup import configure_logging
from papiq.composition.settings import find_unknown_variables, load_settings


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(prog="python -m papiq.composition")
    parser.add_argument("command", nargs="?", choices=["check", "migrate"], default="check")
    command = parser.parse_args(argv).command

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

    if command == "migrate":
        try:
            asyncio.run(migrate_database(settings))
        except Exception:
            log.exception("database migration failed", db_type=settings.db_type)
            return 1
        log.info("database migrated", db_type=settings.db_type)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

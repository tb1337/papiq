"""`python -m papiq.composition [check | migrate | api | worker]`.

- `check` (default): validate the configuration and print it without secrets.
- `migrate`: validate, then bring the configured database to the newest schema. Run before the
  API and the worker start (in the image: `init-migrations`).
- `api`: serve the REST API until SIGTERM or SIGINT. Only with `PAPIQ_ROLE` `all` or `api`.
- `worker`: run the worker service until SIGTERM or SIGINT. Only with `PAPIQ_ROLE` `all` or
  `worker`.

Exits with status 1 and a readable message when the configuration is invalid, the command does
not fit `PAPIQ_ROLE`, or the migration or the service fails.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog

from papiq.composition.api import run_api
from papiq.composition.database import migrate_database
from papiq.composition.endpoints import external_endpoints
from papiq.composition.errors import ConfigurationError
from papiq.composition.logging_setup import configure_logging
from papiq.composition.settings import find_unknown_variables, load_settings
from papiq.composition.worker import run_worker

# Services and the roles that run them.
SERVICES = {"api": {"all", "api"}, "worker": {"all", "worker"}}


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(prog="python -m papiq.composition")
    parser.add_argument(
        "command", nargs="?", choices=["check", "migrate", *SERVICES], default="check"
    )
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
    for variable, host in external_endpoints(settings).items():
        log.warning(
            f"document content is sent to {host}, outside the local network",
            variable=variable,
            host=host,
        )

    if command in SERVICES and settings.role not in SERVICES[command]:
        log.error(
            f"the {command} does not run with PAPIQ_ROLE={settings.role}",
            command=command,
            role=settings.role,
        )
        return 1

    if command in SERVICES:
        run = run_api if command == "api" else run_worker
        try:
            asyncio.run(run(settings))
        except Exception:
            log.exception(f"{command} failed")
            return 1
        return 0

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

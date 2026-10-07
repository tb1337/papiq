"""`python -m papiq.composition [check | migrate | api | worker | evaluate | reindex]`.

- `check` (default): validate the configuration and print it without secrets.
- `migrate`: validate, then bring the configured database to the newest schema. Run before the
  API and the worker start (in the image: `init-migrations`).
- `api`: serve the REST API until SIGTERM or SIGINT. Only with `PAPIQ_ROLE` `all` or `api`.
- `worker`: run the worker service until SIGTERM or SIGINT. Only with `PAPIQ_ROLE` `all` or
  `worker`.
- `evaluate [--fake] [--cases DIR] [--output FILE]`: run the evaluation set (default
  `evaluation`) against the configured language model, or with `--fake` against the set's fixed
  answers, and write a Markdown report. Exits with status 1 if a document came out green that
  should not have or with a wrong value, or a field changed without a passed check.
- `reindex`: rebuild the search index from the database and the object store, in the foreground,
  with progress. The search keeps working meanwhile. Needs `PAPIQ_MEILISEARCH_URL`.

Exits with status 1 and a readable message when the configuration is invalid, the command does
not fit `PAPIQ_ROLE`, or the migration or the service fails.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import structlog

from papiq.adapters.inbound.evaluation import EvaluationSetError
from papiq.composition.api import run_api
from papiq.composition.database import migrate_database
from papiq.composition.endpoints import external_endpoints
from papiq.composition.errors import ConfigurationError
from papiq.composition.evaluation import run_evaluation
from papiq.composition.logging_setup import configure_logging
from papiq.composition.reindex import run_reindex
from papiq.composition.settings import Settings, find_unknown_variables, load_settings
from papiq.composition.worker import run_worker

# Services and the roles that run them.
SERVICES = {"api": {"all", "api"}, "worker": {"all", "worker"}}


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(prog="python -m papiq.composition")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["check", "migrate", *SERVICES, "evaluate", "reindex"],
        default="check",
    )
    parser.add_argument("--fake", action="store_true", help="evaluate: use the fixed answers")
    parser.add_argument(
        "--cases", type=Path, help="evaluate: the evaluation set (default: evaluation)"
    )
    parser.add_argument("--output", type=Path, help="evaluate: where to write the report")
    arguments = parser.parse_args(argv)
    command = arguments.command
    if command != "evaluate" and (arguments.fake or arguments.cases or arguments.output):
        parser.error("--fake, --cases and --output only go with evaluate")

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

    if command == "evaluate":
        return _evaluate(settings, arguments)

    if command == "reindex":
        return _reindex(settings)

    if command == "migrate":
        try:
            asyncio.run(migrate_database(settings))
        except Exception:
            log.exception("database migration failed", db_type=settings.db_type)
            return 1
        log.info("database migrated", db_type=settings.db_type)
    return 0


def _reindex(settings: Settings) -> int:
    log = structlog.get_logger("papiq.composition")
    try:
        result = asyncio.run(run_reindex(settings, progress=True))
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1
    except Exception:
        log.exception("rebuilding the search index failed")
        return 1
    log.info(
        "reindex finished",
        documents=result.documents,
        queued=result.queued,
        removed=result.removed,
    )
    return 0


def _evaluate(settings: Settings, arguments: argparse.Namespace) -> int:
    log = structlog.get_logger("papiq.composition")
    try:
        result = asyncio.run(
            run_evaluation(
                settings,
                cases=arguments.cases or Path("evaluation"),
                fake=arguments.fake,
                output=arguments.output,
                progress=True,
            )
        )
    except (ConfigurationError, EvaluationSetError) as error:
        print(error, file=sys.stderr)
        return 1
    log.info(
        "evaluation finished",
        report=str(result.report),
        cases=len(result.results),
        false_green=result.false_green,
        green_but_wrong=result.green_but_wrong,
        violations=result.violations,
    )
    return 1 if result.false_green or result.green_but_wrong or result.violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

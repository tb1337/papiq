"""The command line: `plan` (trial run), `run` (migration, resumable) and `verify`."""

import argparse
import asyncio
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx2

from papiq_migration import report as reports
from papiq_migration.config import Config, ConfigError, public
from papiq_migration.migrate import Migration, MigrationError, Progress
from papiq_migration.paperless import Paperless, PaperlessError
from papiq_migration.papiq import DELAYS, ApiError, Papiq
from papiq_migration.state import State, StateError
from papiq_migration.verify import verify

DESCRIPTION = """\
Takes over a Paperless-ngx archive into Papiq. It reads Paperless through its REST API and writes
through the Papiq API, with an admin's API token. API keys come from the environment only:
PAPIQ_MIGRATION_PAPERLESS_TOKEN and PAPIQ_MIGRATION_PAPIQ_TOKEN (or ..._FILE with a path), the
addresses from --paperless-url / --papiq-url or PAPIQ_MIGRATION_PAPERLESS_URL / ..._PAPIQ_URL.
"""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="papiq-migration",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = root.add_subparsers(dest="command", required=True)
    for name, text in (
        ("plan", "trial run: read everything, write nothing, report what would happen"),
        ("run", "take everything over; stops cleanly on Ctrl-C and continues where it stopped"),
        ("verify", "compare Paperless and Papiq object by object"),
    ):
        command = commands.add_parser(name, help=text, description=text)
        command.add_argument("--paperless-url", help="e.g. http://localhost:8000")
        command.add_argument("--papiq-url", help="e.g. http://localhost:8001")
        command.add_argument("--state", type=Path, default=Path("migration-state.sqlite"))
        command.add_argument("--report-dir", type=Path, default=Path("migration-reports"))
        command.add_argument("--currency", default="EUR", help="for amounts without a currency")
        command.add_argument("--concurrency", type=int, default=4, help="documents at a time")
        command.add_argument("--limit", type=int, help="only the first N documents (by id)")
        command.add_argument("--pipeline-timeout", type=float, default=3600.0, metavar="SECONDS")
        if name == "verify":
            command.add_argument(
                "--rehash", action="store_true", help="download the originals again and hash them"
            )
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = Config.load(
            paperless_url=args.paperless_url,
            papiq_url=args.papiq_url,
            state=args.state,
            report_dir=args.report_dir,
            currency=args.currency,
            concurrency=args.concurrency,
            limit=args.limit,
            pipeline_timeout=args.pipeline_timeout,
            rehash=getattr(args, "rehash", False),
        )
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return asyncio.run(_with_signals(config, args.command))


async def _with_signals(config: Config, command: str) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(number, _stop, stop, number)
    return await execute(config, command, stop=stop)


def _stop(stop: asyncio.Event, number: signal.Signals) -> None:
    if stop.is_set():
        raise KeyboardInterrupt
    print(
        f"\n{number.name}: stopping after the uploads in progress; run again to continue",
        file=sys.stderr,
    )
    stop.set()


def say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


async def execute(
    config: Config,
    command: str,
    *,
    stop: asyncio.Event | None = None,
    paperless_transport: httpx2.AsyncBaseTransport | None = None,
    papiq_transport: httpx2.AsyncBaseTransport | None = None,
    progress: Progress = say,
    poll: tuple[float, float] = (0.5, 5.0),
    delays: tuple[float, ...] = DELAYS,
) -> int:
    """Run one command; returns the exit code (0 fine, 1 findings or failures, 2 cannot go on,
    130 stopped)."""
    stop = stop or asyncio.Event()
    source = Paperless(config.paperless_url, config.paperless_token, transport=paperless_transport)
    target = (
        Papiq(config.papiq_url, config.papiq_token, transport=papiq_transport, delays=delays)
        if config.papiq_url and config.papiq_token
        else None
    )
    state = State(Path(":memory:") if command == "plan" else config.state)
    try:
        if command != "plan":
            if target is None:
                raise MigrationError(
                    "the Papiq URL and API key are needed (PAPIQ_MIGRATION_PAPIQ_*)"
                )
            state.bind(
                paperless=public(config.paperless_url) or "", papiq=public(config.papiq_url) or ""
            )
        if command == "verify":
            assert target is not None
            result = await verify(config, source, target, state, progress=progress)
            _write(config, "verify", reports.to_markdown(result), reports.to_json(result))
            deviations = len(result["deviations"])
            progress(f"verify: {result['checked']} documents compared, {deviations} deviations")
            return 1 if deviations else 0
        migration = Migration(
            config,
            source,
            target,
            state,
            dry=command == "plan",
            stop=stop,
            progress=progress,
            poll=poll,
        )
        try:
            totals = await migration.execute()
        except MigrationError as error:
            if migration.snapshot is not None:  # what was done so far is in the report
                partial = reports.build(
                    command,
                    migration,
                    state,
                    paperless_url=public(config.paperless_url) or "",
                    papiq_url=public(config.papiq_url),
                )
                _write(config, command, reports.to_markdown(partial), reports.to_json(partial))
            raise error
        result = reports.build(
            command,
            migration,
            state,
            paperless_url=public(config.paperless_url) or "",
            papiq_url=public(config.papiq_url),
        )
        _write(config, command, reports.to_markdown(result), reports.to_json(result))
        progress(
            f"{command}: {totals.documents} documents, {totals.uploaded} uploaded, "
            f"{totals.resumed} resumed, {totals.duplicates} duplicates, {totals.skipped} skipped, "
            f"{totals.failed} failed, {totals.stopped} not started"
        )
        if stop.is_set():
            return 130
        return 1 if totals.failed else 0
    except (MigrationError, PaperlessError, StateError, ApiError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        state.close()
        await source.close()
        if target is not None:
            await target.close()


def _write(config: Config, name: str, markdown: str, json_text: str) -> None:
    config.report_dir.mkdir(parents=True, exist_ok=True)
    (config.report_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    (config.report_dir / f"{name}.json").write_text(json_text, encoding="utf-8")
    say(f"report: {config.report_dir / (name + '.md')}")


if __name__ == "__main__":
    raise SystemExit(main())

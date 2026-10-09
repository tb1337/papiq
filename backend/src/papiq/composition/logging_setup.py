"""Structured logging: readable console output in development, JSON lines in production.

The core logs through the standard library only. structlog is configured here so that stdlib
records (core, Uvicorn, third-party libraries) and structlog events share one format.
"""

import logging
import sys

import structlog

from papiq.composition.settings import Settings

HTTP_CLIENT_LOGGERS = ("httpx", "httpx2", "httpcore")
HEALTH_PATH = "/api/v1/health"


def configure_logging(settings: Settings) -> None:
    """Route all log records through structlog, formatted per `PAPIQ_LOG_FORMAT`."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    # The HTTP clients log the full URL of every request at INFO. Webhook and model URLs may carry
    # a credential in their path or query (n8n, Home Assistant), so only warnings get through.
    for name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
    access = logging.getLogger("uvicorn.access")
    for access_filter in (HideCallbackQuery, DropHealthChecks):
        if not any(isinstance(f, access_filter) for f in access.filters):
            access.addFilter(access_filter())


class DropHealthChecks(logging.Filter):
    """Docker and Compose call `/api/v1/health` every 30 seconds; those calls do not go into the
    access log. Everything else about the health check (a failing one logs a warning) stays."""

    def filter(self, record: logging.LogRecord) -> bool:
        return _request_path(record) != HEALTH_PATH


class HideCallbackQuery(logging.Filter):
    """The OIDC callback's query holds the authorization code and state; the access log shows
    its path only."""

    def filter(self, record: logging.LogRecord) -> bool:
        target = _request_target(record)
        if target is not None and "/oidc/callback" in target and "?" in target:
            args = record.args
            assert isinstance(args, tuple)
            record.args = (*args[:2], target.split("?", 1)[0], *args[3:])
        return True


def _request_target(record: logging.LogRecord) -> str | None:
    """The request target (path and query) of a Uvicorn access record
    (`%s - "%s %s HTTP/%s" %d`)."""
    args = record.args
    if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
        return args[2]
    return None


def _request_path(record: logging.LogRecord) -> str | None:
    target = _request_target(record)
    return None if target is None else target.split("?", 1)[0]

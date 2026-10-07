"""Structured logging: readable console output in development, JSON lines in production.

The core logs through the standard library only. structlog is configured here so that stdlib
records (core, Uvicorn, third-party libraries) and structlog events share one format.
"""

import logging
import sys

import structlog

from papiq.composition.settings import Settings


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
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HideCallbackQuery) for f in access.filters):
        access.addFilter(HideCallbackQuery())


class HideCallbackQuery(logging.Filter):
    """The OIDC callback's query holds the authorization code and state; the access log shows
    its path only."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if "/oidc/callback" in path and "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0], *args[3:])
        return True

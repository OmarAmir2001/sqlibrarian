"""Structured logging with structlog.

Every script calls configure_logging() once, then logs events with fields:

    log = get_logger(__name__)
    log.info("groq.call_done", call=3, pairs=20, tokens=5298)

On a laptop that prints a readable coloured line; with LOG_FORMAT=json each event
is one JSON object (for CI, Kaggle logs or a log store). Logs go to stderr, so a
command's real output (a score table, a prompt) stays alone on stdout.
Never log secrets or real personal data.
"""

import logging
import sys

import structlog

from sqlibrarian.helpers.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)

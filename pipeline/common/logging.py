"""structlog configuration for the pipeline.

Containers render JSON so the Compose and cloud log collectors can parse lines;
a developer terminal renders the console format. The choice is a setting, not a
guess about the runtime, so a container can be switched to console output for
debugging without a code change. This module does not configure the standard
library logging tree that Dagster manages itself.
"""

import logging
import sys
from typing import Literal

import structlog


def configure_logging(log_format: Literal["console", "json"], level: int = logging.INFO) -> None:
    """Configure structlog once for the current process.

    Args:
        log_format: "json" for one JSON object per line, "console" for coloured
            key-value output.
        level: Standard library level below which events are dropped.
    """
    renderer: structlog.types.Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

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


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name, as the style guide requires."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger().bind(component=component)
    return logger

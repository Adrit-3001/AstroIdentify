"""Logging setup for command-line use.

Library modules only create loggers with ``logging.getLogger(__name__)``; they never
configure handlers or print. Applications (the CLI) call :func:`configure_logging`.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(verbosity: int = 0) -> None:
    """Send AstroIdentify log records to stderr.

    Args:
        verbosity: 0 shows warnings and errors, 1 adds info, 2 or more adds debug.
    """
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))

    logger = logging.getLogger("astroidentify")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    # Avoid duplicate output if the root logger is also configured by a host application.
    logger.propagate = False

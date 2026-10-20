"""Logging for simple_grpc: an ``error`` logger for lifecycle
messages and an ``access`` logger for per-RPC lines, both colorized with click.
:func:`configure_logging` fully owns those two loggers; all other loggers,
including the root, are left untouched.
"""

from dataclasses import dataclass
import logging
import os
import sys
from typing import Any
from urllib.parse import unquote

import click
import grpc


logger = logging.getLogger("simple_grpc.error")
access_logger = logging.getLogger("simple_grpc.access")

_LEVEL_STYLES = {
    logging.DEBUG: {"fg": "blue"},
    logging.INFO: {"fg": "green"},
    logging.WARNING: {"fg": "yellow"},
    logging.ERROR: {"fg": "red"},
    logging.CRITICAL: {"fg": "red", "bold": True},
}


def _resolve_level(level: str | int) -> int:
    """
    Map a level name or int to its stdlib numeric value.

    Args:
        level: A level name (``"info"``, ``"WARNING"``, ...) or int.

    Returns:
        The numeric logging level.

    Raises:
        ValueError: If a string level is not a valid logging level name.
    """
    if isinstance(level, int):
        return level
    try:
        return logging.getLevelNamesMapping()[level.upper()]
    except KeyError:
        valid = ", ".join(logging.getLevelNamesMapping())
        raise ValueError(f"invalid log_level {level!r}; valid names: {valid}") from None


def use_colors() -> bool:
    """
    Decide whether ANSI styling should be emitted.

    Returns:
        False when ``NO_COLOR`` is set, True when ``FORCE_COLOR`` is set,
        otherwise whether stderr is a terminal.
    """
    if "NO_COLOR" in os.environ:
        return False
    if "FORCE_COLOR" in os.environ:
        return True
    return sys.stderr.isatty()


def style(text: str, colors: bool, **kwargs: Any) -> str:
    """
    Style text for the terminal when colors are on; identity otherwise.

    Args:
        text: Text to style.
        colors: Whether styling applies (``LoggingConfig.use_colors``).
        **kwargs: Forwarded to ``click.style`` (``fg``, ``bold``, ...).

    Returns:
        The styled text, or ``text`` unchanged when colors are off.
    """
    return click.style(text, **kwargs) if colors else text


@dataclass
class LoggingConfig:
    """
    Logging knobs for serving.

    ``log_level`` and ``use_colors`` are resolved eagerly at construction —
    like ``ServerCredentials``, bad values fail fast, not at serve time.
    """

    access_log: bool = True
    log_level: str | int = "info"
    use_colors: bool | None = None

    def __post_init__(self) -> None:
        """
        Resolve ``log_level`` to an int and ``use_colors`` to a bool.

        Raises:
            ValueError: If ``log_level`` is an unknown level name.
        """
        self.log_level = _resolve_level(self.log_level)
        self.use_colors = use_colors() if self.use_colors is None else self.use_colors


class ColourFormatter(logging.Formatter):
    """Format records as a colored, padded level prefix plus the message."""

    def __init__(self, fmt: str, use_colors: bool) -> None:
        """
        Store the format string and whether to colorize level names.

        Args:
            fmt: Format string; may reference the injected ``levelprefix``.
            use_colors: Whether to style the level prefix with click.
        """
        super().__init__(fmt)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """
        Render one record with a ``levelprefix``.

        Args:
            record: The record to format.

        Returns:
            The formatted line, e.g. ``"INFO:    serving on ..."``.
        """
        prefix = f"{record.levelname}:".ljust(8)
        if self.use_colors:
            prefix = click.style(prefix, **_LEVEL_STYLES.get(record.levelno, {}))
        record.levelprefix = prefix  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging(config: LoggingConfig) -> None:
    """(Re)configure the two loggers per ``config`` with colored handlers.

    Owns ``simple_grpc.error`` and ``simple_grpc.access`` completely — any
    pre-existing handlers on them are replaced. Users get full control by
    adjusting the loggers *after* serving starts, or by never letting
    simple_grpc serve console output through them.

    Args:
        config: Level, colors, and access-log knobs (``LoggingConfig``).
    """
    colors = config.use_colors
    for lg, stream in ((logger, sys.stderr), (access_logger, sys.stdout)):
        lg.handlers.clear()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(ColourFormatter("%(levelprefix)s %(message)s", colors))
        lg.addHandler(handler)
        lg.setLevel(config.log_level)
        lg.propagate = False

    # stdlib kill switch; owns the access_log knob instead of threading it.
    access_logger.disabled = not config.access_log


def log_access(
    context: grpc.ServicerContext,
    path: str,
    code: grpc.StatusCode,
) -> None:
    """
    Emit one access log line through ``access_logger``; no-op when the
    logger is disabled (``configure_logging`` sets ``disabled`` from
    ``LoggingConfig.access_log``).

    Args:
        context: The servicer context, for the client peer address.
        path: Full gRPC method path (``/pkg.Service/Method``).
        code: The gRPC status the RPC finished with.
    """
    if access_logger.disabled:
        return

    peer = context.peer()
    if isinstance(peer, str):
        peer = unquote(peer)

    access_logger.info('%s - "POST %s HTTP/2.0" %s', peer, path, code.name)

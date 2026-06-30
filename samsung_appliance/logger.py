"""Stdout logging — works correctly under Docker's PYTHONUNBUFFERED=1.

Two logger families share the root handler:
  * `samsung_appliance` (and its children) for module-level lines —
    DTLS warnings, generic startup chatter, MQTT plumbing.
  * `<class>.<serial>` for per-appliance bridge lines — e.g.
    `dryer.<serial>`. Each PushBridge gets its own such logger
    via bridge_logger() once the seed reveals the appliance's serial.

Both trees propagate to the root logger, which is the one with the
StreamHandler, so the same format applies everywhere.

WARNING lines render yellow, ERROR red, when colour is enabled. Set
`NO_COLOR=1` in the environment to fall back to plain text (e.g. when
writing logs to a file)."""
import logging
import os
import sys


_LEVEL_COLOURS = {
    logging.WARNING:  '\033[33m',   # yellow
    logging.ERROR:    '\033[31m',   # red
    logging.CRITICAL: '\033[1;31m', # bold red
}
_RESET = '\033[0m'


def _colour_enabled() -> bool:
    return 'NO_COLOR' not in os.environ


class _LevelColourFormatter(logging.Formatter):
    """Wraps the rendered line in an ANSI colour for WARNING+ levels.
    INFO/DEBUG pass through unchanged so the bulk of the log stays
    readable and a yellow line draws the eye."""

    def __init__(self, fmt: str, datefmt: str, use_colour: bool):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if not self.use_colour:
            return line
        colour = _LEVEL_COLOURS.get(record.levelno)
        if colour is None:
            return line
        return f"{colour}{line}{_RESET}"


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_LevelColourFormatter(
    fmt='%(asctime)s  %(levelname)-5s  %(name)-26s  %(message)s',
    datefmt='%H:%M:%S',
    use_colour=_colour_enabled(),
))

logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)

logger = logging.getLogger("samsung_appliance")


def bridge_logger(klass: str, serial: str | None = None) -> logging.Logger:
    """Return a top-level logger tagged with the appliance class and,
    once known, the serial. Pre-seed callers pass serial=None and get
    e.g. `dryer`; post-seed callers pass the serial and get e.g.
    `dryer.<serial>`."""
    name = f"{klass}.{serial}" if serial else klass
    return logging.getLogger(name)

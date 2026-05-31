"""Stdout logging — works correctly under Docker's PYTHONUNBUFFERED=1.

Two logger families share the root handler:
  * `samsung_appliance` (and its children) for module-level lines —
    DTLS warnings, generic startup chatter, MQTT plumbing.
  * `<class>.<serial>` for per-appliance bridge lines — e.g.
    `dryer.<serial>`. Each PushBridge gets its own such logger
    via bridge_logger() once the seed reveals the appliance's serial.

Both trees propagate to the root logger, which is the one with the
StreamHandler, so the same format applies everywhere."""
import logging
import sys


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-5s  %(name)-26s  %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("samsung_appliance")


def bridge_logger(klass: str, serial: str | None = None) -> logging.Logger:
    """Return a top-level logger tagged with the appliance class and,
    once known, the serial. Pre-seed callers pass serial=None and get
    e.g. `dryer`; post-seed callers pass the serial and get e.g.
    `dryer.<serial>`."""
    name = f"{klass}.{serial}" if serial else klass
    return logging.getLogger(name)

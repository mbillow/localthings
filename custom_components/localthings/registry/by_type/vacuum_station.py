"""Stick-vacuum clean/auto-empty station device registry (issues #131 / #219).

Station dustbag/dustbin/UV-sanitize state plus, when present (VS9700),
wand battery/charging via `/status/stick/vs/0`. No suction/room-map control.
"""

from ..capabilities import common, ignored, vacuum_station
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="vacuum_station",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            vacuum_station.DUSTBAG,
            vacuum_station.DUSTBAG_USAGE,
            vacuum_station.DUSTBIN_SETTING,
            vacuum_station.CLEANSTATION_STATUS,
            vacuum_station.STICK_BODY,
        ]
    ),
)

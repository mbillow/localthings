"""Robot vacuum device registry (POWERbot R7040, issue #659).

A different product from `vacuum_station`, which models a stick vacuum's
clean station and has no vacuum body to drive.
"""

from ..capabilities import common, ignored, robot_vacuum
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="robot_vacuum",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            robot_vacuum.VACUUM,
            robot_vacuum.BATTERY,
            robot_vacuum.BATTERY_VS_FALLBACK,
            robot_vacuum.MOVEMENT,
        ]
    ),
)

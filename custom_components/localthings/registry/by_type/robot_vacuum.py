"""Robot vacuum device registry (POWERbot R7040, issue #659).

A different product from `vacuum_station`, which models a stick vacuum's
clean station and has no vacuum body to drive.
"""

from ..capabilities import air_purifier, common, ignored, robot_vacuum
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
            # Jet Bot Combo (JETBOT_COMBOT_9X00_24K, #838).
            robot_vacuum.BATTERY_HEALTH,
            robot_vacuum.DUST_BAG,
            robot_vacuum.DOCK_FILTER,
            robot_vacuum.MOTOR_FILTER,
            robot_vacuum.LIGHTING,
            robot_vacuum.CHILD_LOCK,
            robot_vacuum.FIND_ROBOT,
            robot_vacuum.DO_NOT_DISTURB,
            robot_vacuum.STATION,
            # Same /settings/sound/volume/vs/0 shape as the air purifiers.
            air_purifier.SOUND_VOLUME,
            *robot_vacuum.COVERAGE,
        ]
    ),
)

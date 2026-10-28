"""Air Monitor Plus device registry (issue #210).

A standalone, battery-powered air-quality sensor puck (ASM-KR-TP1-22-*
board) -- no controllable state at all beyond the do-not-disturb window,
so this registry is almost entirely sensors. No common.POWER: there's no
`/power/*` resource on this board, only `/energy/battery/vs/0`.
"""

from ..capabilities import air_monitor, airconditioner, common, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="air_monitor",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *air_monitor.COVERAGE,
            air_monitor.SENSORS,
            air_monitor.HUMIDITY,
            # Some units report the Tizen Lite AC's items[] temperature (#683).
            airconditioner.CURRENT_TEMPERATURE_VS,
            air_monitor.BATTERY,
            air_monitor.AIR_QUALITY_STANDARD,
            air_monitor.DND,
        ]
    ),
)

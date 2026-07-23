"""Range-hood device registry."""

from ..capabilities import common, ignored, range_hood
from ._base import DeviceRegistry, _build


REGISTRY = DeviceRegistry(
    name='range_hood',
    capabilities=_build([
        *ignored.IGNORED,
        range_hood.HOOD_ALARMS,
        common.ENERGY_METER,
        common.FIRMWARE_UPDATE,
        range_hood.HOOD_FAN,
        range_hood.HOOD_LAMP,
        range_hood.HOOD_FILTER,
        range_hood.AIR_QUALITY,
        range_hood.AIR_LEVEL_CHECK,
        range_hood.AUTO_VENTILATION,
        *range_hood.COVERAGE,
    ]),
)

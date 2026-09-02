"""Range-hood device registry.

Includes HOOD_STATUS/HOOD_SPEC (the DAWIT 3.0 generation bare-field
/hood/status/vs/0 + /hood/spec/vs/0 shape, added for the built-in vent
fan on a combi microwave in by_type/microwave.py) so a standalone hood
of the same board generation is covered too, not just when paired with a
microwave -- no such dump exists yet, but the capabilities are generic
vent-hood ones with nothing microwave-specific in them."""

from ..capabilities import common, ignored, range_hood
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="range_hood",
    capabilities=_build(
        [
            *ignored.IGNORED,
            range_hood.HOOD_ALARMS,
            common.ENERGY_METER,
            common.FIRMWARE_UPDATE,
            # registry.PROBE_HREFS is global, so every registry has to cover
            # these or a board that answers the probe raises a spurious
            # coverage-gap Repair (issue #301).
            common.FILE_LIST,
            common.FILE_TRANSFER,
            range_hood.AFTER_RUN,
            range_hood.HOOD_FAN,
            range_hood.HOOD_LAMP,
            range_hood.HOOD_FILTER,
            range_hood.AIR_QUALITY,
            range_hood.AIR_LEVEL_CHECK,
            range_hood.AUTO_VENTILATION,
            range_hood.HOOD_STATUS,
            range_hood.HOOD_SPEC,
            *range_hood.COVERAGE,
        ]
    ),
)

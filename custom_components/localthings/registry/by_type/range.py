"""Range (oven + cooktop combo) device registry — issue #44.

Reuses the oven family's cavity/setpoint/mode/operational-state/door/
connected capabilities wholesale (a range's oven half is the same OCF
surface as a standalone oven) and adds the cooktop-specific capabilities
for the burner half.
"""
from ..capabilities import common, ignored, oven
from ..capabilities import range as range_caps
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='range',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        oven.OVEN_CAVITY,
        oven.OVEN_SETPOINT,
        oven.OVEN_MODE,
        oven.OVEN_OPERATIONAL_STATE,
        oven.OVEN_DOOR,
        oven.OVEN_CONNECTED,
        oven.OVEN_SPEC,
        range_caps.COOKTOP_STATUS,
        range_caps.COOKTOP_SPEC,
        range_caps.COOKTOP_SAFETY,
    ]),
)

"""Range (oven + cooktop combo) device registry — issue #44.

Reuses the oven family's cavity/setpoint/mode/operational-state/door/
connected capabilities wholesale (a range's oven half is the same OCF
surface as a standalone oven) and adds the cooktop-specific capabilities
for the burner half.
"""

from ..capabilities import common, dishwasher, ignored, oven
from ..capabilities import range as range_caps
from ..capability import Capability
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="range",
    capabilities=_build(
        [
            *ignored.without("/configuration/vs/0"),
            *common.UNIVERSAL,
            *common.POWER,
            oven.OVEN_CAVITY,
            oven.OVEN_SETPOINT,
            oven.OVEN_TEMP_CURRENT_OCF,
            oven.OVEN_TEMP_DESIRED_OCF,
            oven.OVEN_PROBE_CURRENT_OCF,
            oven.OVEN_PROBE_DESIRED_OCF,
            oven.OVEN_MODE,
            oven.OVEN_OPERATIONAL_STATE,
            oven.OVEN_DOOR,
            oven.OVEN_CONNECTED,
            oven.OVEN_SPEC,
            oven.OVEN_RECIPE_COOK,
            # A TP2X gas range (issue #670) lists a fan-speed range with no
            # current speed and grill-area ids with no state; it has no hood.
            Capability(href="/hood/fanspeed/vs/0"),
            Capability(href="/multigrills/vs/0"),
            # The diagnosisStart shape the oven registry binds (#300), here on
            # an NX58K7850SS Flex Duo's two cavities (#624).
            dishwasher.DIAGNOSIS,
            range_caps.RANGE_CLOCK_SYNC,
            range_caps.COOKTOP_STATUS,
            range_caps.COOKTOP_SPEC,
            range_caps.COOKTOP_SAFETY,
            range_caps.COOKTOP_MONITORING,
        ]
    ),
)

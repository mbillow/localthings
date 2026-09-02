"""Microwave device registry (combi and plain microwaves, issues #66/#121).

Shares the oven board family's cavity/cook-cycle resource shape, so the
operational-state, door, cloud-connected, and quick-recipe-display
Capability objects are reused directly from oven.py rather than duplicated.
Cooking mode, setpoint, cavity power level, and lamp are genuinely
different for this family (different mode vocabulary, different setpoint
bounds, an extra powerLevel field, a differently-named lamp option) and are
defined fresh in capabilities/microwave.py -- see that module's docstring.

Some combi units (built-in over-the-range microwaves, issues #137/#142)
also carry the vent fan's `/hood/fanspeed/vs/0` resource, in the exact same
shape a standalone range hood reports it in -- reused directly from
range_hood.py rather than duplicated. Unlike a standalone hood, this dump
has no sibling `/power/0` or `/power/vs/0` resource; fan.py's
LocalThingsRangeHoodFan falls back to treating fan speed 0 as off in that
case (see its `_speed_zero_is_off` check).

A DAWIT 3.0 generation board (issue #433) answers none of the hrefs above
at all -- it carries its own `/oven/status/vs/0`/`/oven/spec/vs/0`/
`/oven/settings/status/vs/0` cavity resources (microwave.MICROWAVE_STATUS/
MICROWAVE_SPEC/MICROWAVE_SETTINGS) and its own `/hood/status/vs/0`/
`/hood/spec/vs/0` vent resources (range_hood.HOOD_STATUS/HOOD_SPEC), added
alongside the older hrefs above rather than replacing them -- discovery
binds whichever generation's hrefs a given device actually reports.
"""

from ..capabilities import common, ignored, microwave, oven, range_hood
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="microwave",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            microwave.MICROWAVE_CAVITY,
            microwave.MICROWAVE_SETPOINT,
            microwave.MICROWAVE_MODE,
            oven.OVEN_OPERATIONAL_STATE,
            oven.OVEN_DOOR,
            oven.OVEN_CONNECTED,
            oven.OVEN_RECIPE_COOK,
            range_hood.HOOD_FAN,
            microwave.MICROWAVE_STATUS,
            microwave.MICROWAVE_SPEC,
            microwave.MICROWAVE_SETTINGS,
            range_hood.HOOD_STATUS,
            range_hood.HOOD_SPEC,
        ]
    ),
)

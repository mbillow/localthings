from . import common, fridge, laundry, operational, oven
from ..capability import Capability


def _is_capability(v):
    return isinstance(v, Capability)


# Oven capabilities with hrefs unique to the oven family.
# OVEN_OPERATIONAL_STATE (/operational/state/vs/0) and OVEN_DOOR
# (/doors/vs/0) are intentionally excluded — those hrefs are already
# covered by the shared OPERATIONAL_STATE and fridge DOORS_STATUS
# capabilities. Oven-specific entities from those hrefs (cook_time,
# door sensor) will be wired by oven-specific discovery in Task 13.
_OVEN_GLOBAL_CAPS = [
    oven.OVEN_SETPOINT,
    oven.OVEN_CAVITY,
    oven.OVEN_MODE,
]

ALL = [v for mod in (common, operational, laundry, fridge)
       for v in vars(mod).values() if _is_capability(v)] + _OVEN_GLOBAL_CAPS

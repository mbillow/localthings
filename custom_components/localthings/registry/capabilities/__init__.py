from . import common, dishwasher, fridge, ignored, laundry, operational, oven
from ..capability import Capability


def _is_capability(v):
    return isinstance(v, Capability)


# Only OVEN_CAVITY (/oven/vs/0) is safe to include globally — that href
# is oven-unique. OVEN_SETPOINT (/temperatures/vs/0) and OVEN_MODE
# (/mode/vs/0) collide with fridge hrefs that share the same path but
# have different schemas. Those capabilities require rt_filter or class-
# scoped registry support before they can be added back to ALL.
_OVEN_GLOBAL_CAPS = [
    oven.OVEN_CAVITY,
]

ALL = [v for mod in (common, operational, laundry, fridge)
       for v in vars(mod).values() if _is_capability(v)] + _OVEN_GLOBAL_CAPS + ignored.IGNORED

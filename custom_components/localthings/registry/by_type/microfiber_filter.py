"""Microfiber filter device registry (issues #475, #626).

Shared the washer registry until #626, which left it with a cycle select,
delay, start and child-lock entities it has no hardware behind.
"""

from ..capabilities import common, cooktop, ignored, microfiber_filter
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="microfiber_filter",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *(c for c in common.UNIVERSAL if c.href not in ("/kidslock/0", "/kidslock/vs/0")),
            *common.POWER,
            common.WATER_FILTER,
            common.FILTER_STATUS,
            microfiber_filter.FILTER_OPERATIONAL_STATE,
            # Status only, as on the cooktop: both dumps report an empty
            # /diagnosis/vs/0, so a start write has nothing to go on.
            cooktop.DIAGNOSIS,
            *microfiber_filter.COVERAGE,
        ]
    ),
)

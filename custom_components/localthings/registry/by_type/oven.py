"""Oven device registry."""

from ..capabilities import common, dishwasher, ignored, oven
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="oven",
    capabilities=_build(
        [
            *ignored.IGNORED,
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
            # issue #300: /diagnosis/vs/0 is the same diagnosisStart shape
            # dishwasher.py and airconditioner.py already reuse.
            dishwasher.DIAGNOSIS,
        ]
    ),
)

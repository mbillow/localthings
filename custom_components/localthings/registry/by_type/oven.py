"""Oven device registry."""

from ..capabilities import common, dishwasher, ignored, oven
from ..capabilities import range as range_caps
from ._base import DeviceRegistry, _build, read_only_when

REGISTRY = DeviceRegistry(
    name="oven",
    capabilities=_build(
        [
            part
            for cap in [
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
                # issue #300: /diagnosis/vs/0 is the same diagnosisStart shape
                # dishwasher.py and airconditioner.py already reuse.
                dishwasher.DIAGNOSIS,
                # The range's clock write (#404), confirmed to set the clock on
                # an NV7B4445VAK wall oven.
                range_caps.RANGE_CLOCK_SYNC,
            ]
            # The combo's oven cavity (#674).
            for part in read_only_when(cap, oven.is_tp6x_ov_board)
        ]
    ),
)

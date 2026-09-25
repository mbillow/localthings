"""Washer device registry."""

from ..capabilities import common, dishwasher, ignored, laundry, washer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="washer",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            washer.WASHER_SETTINGS,
            washer.WASHER_COURSE,
            laundry.BUZZER_SOUND,
            laundry.JOB_BEGINNING_STATUS,
            common.WATER_METER,
            # Microfiber laundry-lint filter unit (AMF, issue #475) shares
            # this registry and adds a water/microfiber filter plus a
            # blockage notice; harmless on plain washers, which don't report
            # either href.
            common.WATER_FILTER,
            common.FILTER_STATUS,
            washer.WASHER_OPERATIONAL_STATE,
            dishwasher.DIAGNOSIS,
        ]
    ),
)

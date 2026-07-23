"""Washer device registry."""
from ..capabilities import common, dishwasher, ignored, laundry, operational, washer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='washer',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        washer.WASHER_SETTINGS,
        washer.WASHER_COURSE,
        laundry.BUZZER_SOUND,
        laundry.JOB_BEGINNING_STATUS,
        common.WATER_METER,
        operational.OPERATIONAL_STATE,
        dishwasher.DIAGNOSIS,
    ]),
)

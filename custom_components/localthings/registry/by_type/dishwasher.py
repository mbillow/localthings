"""Dishwasher device registry."""
from ..capabilities import common, dishwasher, ignored, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dishwasher',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        common.WATER_METER,
        common.WATER_FILTER,
        operational.OPERATIONAL_STATE,
        dishwasher.CYCLE_OPTIONS,
        dishwasher.DISHWASHER_SETTINGS,
        dishwasher.DIAGNOSIS,
        dishwasher.OPERATION_ORIGIN,
        laundry.JOB_BEGINNING_STATUS,
        laundry.DOOR_LED,
        laundry.SOUND_MODE,
        laundry.SOUND_VOLUME,
    ]),
)

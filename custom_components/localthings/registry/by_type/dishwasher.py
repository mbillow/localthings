"""Dishwasher device registry."""
from ..capabilities import common, dishwasher, fridge, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dishwasher',
    capabilities=_build([
        common.POWER,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
        common.ALARMS,
        common.ENERGY_METER,
        common.WATER_METER,
        common.WATER_FILTER,
        operational.OPERATIONAL_STATE,
        dishwasher.CYCLE_OPTIONS,
        dishwasher.DISHWASHER_SETTINGS,
        laundry.DOOR_LED,
        laundry.SOUND_MODE,
        laundry.SOUND_VOLUME,
        fridge.FIRMWARE_UPDATE,
    ]),
)

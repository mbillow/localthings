"""Dishwasher device registry."""
from ..capabilities import common, fridge, laundry, operational
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
        laundry.DOOR_LED,
        laundry.SOUND_MODE,
        fridge.FIRMWARE_UPDATE,
    ]),
)

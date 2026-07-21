"""Dishwasher device registry."""
from ..capabilities import common, dishwasher, fridge, ignored, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dishwasher',
    capabilities=_build([
        *ignored.IGNORED,
        common.POWER_GENERIC,
        common.POWER_VS_FALLBACK,
        common.KIDS_LOCK_GENERIC,
        common.KIDS_LOCK_VS_FALLBACK,
        common.REMOTE_CONTROL_GENERIC,
        common.REMOTE_CONTROL_VS_FALLBACK,
        common.ALARMS,
        common.ENERGY_METER,
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
        fridge.FIRMWARE_UPDATE,
    ]),
)

"""Washer device registry."""
from ..capabilities import common, dishwasher, fridge, ignored, laundry, operational, washer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='washer',
    capabilities=_build([
        *ignored.IGNORED,
        common.POWER_GENERIC,
        common.POWER_VS_FALLBACK,
        common.KIDS_LOCK_GENERIC,
        common.KIDS_LOCK_VS_FALLBACK,
        common.REMOTE_CONTROL_GENERIC,
        common.REMOTE_CONTROL_VS_FALLBACK,
        washer.WASHER_SETTINGS,
        washer.WASHER_COURSE,
        laundry.BUZZER_SOUND,
        laundry.JOB_BEGINNING_STATUS,
        common.ALARMS,
        common.ENERGY_METER,
        common.WATER_METER,
        operational.OPERATIONAL_STATE,
        dishwasher.DIAGNOSIS,
        fridge.FIRMWARE_UPDATE,
    ]),
)

"""Washer device registry."""
from ..capabilities import common, dishwasher, fridge, ignored, operational, washer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='washer',
    capabilities=_build([
        *ignored.IGNORED,
        washer.POWER_GENERIC,
        washer.POWER_VS_FALLBACK,
        washer.KIDS_LOCK_GENERIC,
        washer.KIDS_LOCK_VS_FALLBACK,
        washer.REMOTE_CONTROL_GENERIC,
        washer.REMOTE_CONTROL_VS_FALLBACK,
        washer.WASHER_SETTINGS,
        washer.WASHER_COURSE,
        washer.BUZZER_SOUND,
        washer.WASHER_JOB_BEGINNING_STATUS,
        common.ALARMS,
        common.ENERGY_METER,
        common.WATER_METER,
        operational.OPERATIONAL_STATE,
        dishwasher.DIAGNOSIS,
        fridge.FIRMWARE_UPDATE,
    ]),
)

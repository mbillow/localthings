"""Oven device registry."""
from ..capabilities import common, fridge, oven
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='oven',
    capabilities=_build([
        common.POWER,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
        common.ALARMS,
        oven.OVEN_CAVITY,
        oven.OVEN_SETPOINT,
        oven.OVEN_MODE,
        oven.OVEN_OPERATIONAL_STATE,
        oven.OVEN_DOOR,
        oven.OVEN_CONNECTED,
        fridge.FIRMWARE_UPDATE,
    ]),
)

"""Oven device registry."""
from ..capabilities import common, ignored, oven
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='oven',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        oven.OVEN_CAVITY,
        oven.OVEN_SETPOINT,
        oven.OVEN_MODE,
        oven.OVEN_OPERATIONAL_STATE,
        oven.OVEN_DOOR,
        oven.OVEN_CONNECTED,
        oven.OVEN_SPEC,
    ]),
)

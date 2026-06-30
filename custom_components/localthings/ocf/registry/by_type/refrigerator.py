"""Refrigerator device registry."""
from ..capabilities import common, fridge
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='refrigerator',
    capabilities=_build([
        common.POWER,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
        common.ALARMS,
        common.ENERGY_METER,
        common.WATER_FILTER,
        fridge.DOORS_STATUS,
        fridge.ICEMAKER_STATUS,
        fridge.REFRIGERATION,
        fridge.AUTOFILL,
        fridge.CABINET_LIGHT,
        fridge.SABBATH,
        fridge.BEVERAGE_ZONE,
        fridge.FIRMWARE_UPDATE,
    ]),
    pattern_capabilities=[
        fridge.TEMP_CURRENT_GENERIC,
        fridge.TEMP_SETPOINT_GENERIC,
        fridge.ICEMAKER_GENERIC,
        fridge.DOOR_GENERIC,
    ],
)

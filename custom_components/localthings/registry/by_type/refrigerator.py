"""Refrigerator device registry."""
from ..capabilities import common, fridge
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='refrigerator',
    capabilities=_build([
        common.POWER,
        fridge.STATUS_LOCK,
        fridge.DOOR_ALERT,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
        common.ALARMS,
        common.ENERGY_METER,
        common.WATER_FILTER,
        fridge.ICEMAKER_NIGHTTIME,
        fridge.FLEX_ZONE,
        fridge.REFRIGERATION,
        fridge.AUTOFILL,
        fridge.WELCOME_LIGHTING,
        fridge.CABINET_LIGHT,
        fridge.CABINET_LIGHT_ENHANCED,
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

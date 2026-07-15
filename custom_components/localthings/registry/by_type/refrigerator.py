"""Refrigerator device registry."""
from ..capabilities import common, fridge, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='refrigerator',
    capabilities=_build([
        *ignored.IGNORED,
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
        fridge.DEFROST_DELAY,
        fridge.DEFROST_BLOCK_STATUS,
        fridge.SELF_CHECK,
        fridge.DOORS_FALLBACK,
        fridge.TEMPERATURES_FALLBACK,
        fridge.ICEMAKER_STATUS_FALLBACK,
        fridge.REFRIGERATION_FALLBACK,
    ]),
    pattern_capabilities=[
        fridge.TEMP_CURRENT_GENERIC,
        fridge.TEMP_SETPOINT_GENERIC,
        fridge.ICEMAKER_GENERIC,
        fridge.DOOR_GENERIC,
    ],
)

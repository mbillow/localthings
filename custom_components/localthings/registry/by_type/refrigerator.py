"""Refrigerator device registry."""
from ..capabilities import common, dishwasher, fridge, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='refrigerator',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        fridge.STATUS_LOCK,
        fridge.DOOR_ALERT,
        common.WATER_FILTER,
        dishwasher.DIAGNOSIS,
        fridge.ICEMAKER_NIGHTTIME,
        fridge.FLEX_ZONE,
        fridge.REFRIGERATION,
        fridge.AUTOFILL,
        fridge.WELCOME_LIGHTING,
        fridge.CABINET_LIGHT,
        fridge.CABINET_LIGHT_ENHANCED,
        fridge.SABBATH,
        fridge.BEVERAGE_ZONE,
        fridge.PANTRY_ZONE,
        fridge.DEFROST_DELAY,
        fridge.DEFROST_DELAY_NATIVE_DUPLICATE,
        fridge.DEFROST_BLOCK_STATUS,
        fridge.DOORS_FALLBACK,
        fridge.TEMPERATURES_FALLBACK,
        fridge.ICEMAKER_STATUS_FALLBACK,
        fridge.ICEMAKER_STATUS_NATIVE_DUPLICATE,
        fridge.REFRIGERATION_FALLBACK,
    ]),
    pattern_capabilities=[
        fridge.TEMP_CURRENT_GENERIC,
        fridge.TEMP_SETPOINT_GENERIC,
        fridge.ICEMAKER_GENERIC,
        fridge.DOOR_GENERIC,
    ],
)

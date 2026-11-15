"""Refrigerator device registry."""

from ..capabilities import common, dishwasher, fridge, ignored, water_purifier
from ..capability import Capability
from ._base import DeviceRegistry, _build

# The Family Hub screen's own resources (issue #823's 25K_REF_LCD_FHUB10.0),
# none of them refrigeration state.
_FAMILY_HUB_SCREEN = (
    # Display accessibility (voice guide, grayscale, color inversion, font
    # size) in the nested {value, supportedValues} shape the washer panel
    # reports too, with no write contract seen on either.
    Capability(href="/accessibility/vs/0"),
    # The photo frame's content list: file ids, MIME types and expiry
    # times of the images the screen shows.
    Capability(href="/udo/contents/provider/vs/0"),
)

REGISTRY = DeviceRegistry(
    name="refrigerator",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *_FAMILY_HUB_SCREEN,
            *common.UNIVERSAL,
            *common.POWER,
            fridge.STATUS_LOCK,
            fridge.DOOR_ALERT,
            # Issue #495's AILITE_REF_25K: same voice/fixedTone/mute sound
            # mode, alarmInMute output and min/max/resolution volume shapes
            # the water purifier reports -- each reads its own live
            # supported list/range, so it's reused rather than copied.
            water_purifier.SOUND_MODE,
            water_purifier.SOUND_OUTPUT,
            water_purifier.SOUND_VOLUME,
            common.WATER_FILTER,
            fridge.AIR_FILTER,
            fridge.DEODOR_FILTER,
            fridge.AUTO_DOOR_TIMER,
            fridge.WINECELLAR_PANTRY_ZONE,
            fridge.WINECELLAR_INFO,
            dishwasher.DIAGNOSIS,
            fridge.ICEMAKER_NIGHTTIME,
            fridge.DEFROST_PREDICTION,
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
            fridge.DEFINITE_TEMPERATURE_COOLER,
            fridge.DEFINITE_TEMPERATURE_FREEZER,
            fridge.DOORS_FALLBACK,
            fridge.TEMPERATURES_FALLBACK,
            fridge.ICEMAKER_STATUS_FALLBACK,
            fridge.ICEMAKER_STATUS_NATIVE_DUPLICATE,
            fridge.REFRIGERATION_FALLBACK,
        ]
    ),
    pattern_capabilities=[
        fridge.TEMP_CURRENT_GENERIC,
        fridge.TEMP_SETPOINT,
        fridge.ICEMAKER_GENERIC,
        fridge.DOOR_GENERIC,
        fridge.KIMCHI_ZONE,
        fridge.KIMCHI_DOOR_GENERIC,
        fridge.AUTO_DOOR_VARIANT,
    ],
)

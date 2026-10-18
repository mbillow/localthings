"""Washer device registry."""

from ..capabilities import common, dishwasher, ignored, laundry, washer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="washer",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            washer.WASHER_SETTINGS,
            washer.WASHER_COURSE,
            laundry.BUZZER_SOUND,
            laundry.JOB_BEGINNING_STATUS,
            common.WATER_METER,
            # Inert on washers that don't report either href; kept so a
            # washer reporting a filter still binds it. The AMF filter has
            # its own registry now (#626).
            common.WATER_FILTER,
            common.FILTER_STATUS,
            washer.WASHER_OPERATIONAL_STATE,
            dishwasher.DIAGNOSIS,
        ]
    ),
)

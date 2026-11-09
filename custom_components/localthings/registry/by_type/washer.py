"""Washer device registry."""

from ..capabilities import (
    air_purifier,
    common,
    dishwasher,
    ignored,
    laundry,
    washer,
    water_purifier,
)
from ..capability import Capability
from ._base import DeviceRegistry, _build

# Sound settings on the AWM-US-M64 washer-dryer (issue #784). Output and
# volume reuse the families whose shapes match: deviceType+alarmInMute, and a
# device-reported min/max/resolution (resolution 1 here, laundry.SOUND_VOLUME
# hardcodes the dishwasher's 5).
_SOUND = (
    laundry.SOUND_MODE,
    laundry.SOUND_TONE,
    air_purifier.SOUND_VOLUME,
    water_purifier.SOUND_OUTPUT,
)

_IGNORED_HERE = (
    # supportedVoices are opaque voice-pack IDs ("102"/"103"); same reasoning
    # as airconditioner.py's ignore of this href.
    Capability(href="/settings/sound/voice/vs/0"),
    # Panel display accessibility (voice guide, grayscale, high contrast,
    # font size) in a nested {value, supportedValues} shape with no write
    # contract seen on any board yet.
    Capability(href="/accessibility/vs/0"),
)

REGISTRY = DeviceRegistry(
    name="washer",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            *_SOUND,
            *_IGNORED_HERE,
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

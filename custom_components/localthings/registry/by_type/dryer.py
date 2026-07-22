"""Dryer device registry.

Note: dryer devices use port 49155 (not 49154). The config flow handles
this automatically via port probing.

The power/kids-lock/remote-control, buzzer, energy-meter, job-status, and
cycle-select capabilities are the shared laundry ones (laundry.py), the same
objects the washer registry uses -- washer and dryer expose the same
DA_WM_-family surface, so they stay consistent instead of each carrying a
bespoke variant.
"""
from ..capabilities import common, dryer, ignored, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dryer',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        operational.OPERATIONAL_STATE,
        laundry.DOOR_LED,
        laundry.SOUND_MODE,
        laundry.BUZZER_SOUND,
        laundry.JOB_BEGINNING_STATUS,
        dryer.DRYER_SETTINGS,
        dryer.DRYER_COURSE,
        dryer.DRYER_DIAGNOSIS,
    ]),
)

"""Dryer device registry.

Note: dryer devices use port 49155 (not 49154). Pass default_port=49155
when calling build_runtime_descriptor() for this registry.
"""
from ..capabilities import common, dryer, fridge, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dryer',
    capabilities=_build([
        common.POWER,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
        common.ALARMS,
        common.ENERGY_METER,
        operational.OPERATIONAL_STATE,
        laundry.DOOR_LED,
        laundry.SOUND_MODE,
        dryer.DRYER_SETTINGS,
        dryer.DRYER_COURSE,
        dryer.JOB_BEGINNING_STATUS,
        dryer.DRYER_DIAGNOSIS,
        fridge.FIRMWARE_UPDATE,
    ]),
)

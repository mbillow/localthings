"""Cooktop device registry."""

from ..capabilities import common, cooktop, ignored
from ._base import DeviceRegistry, _build


REGISTRY = DeviceRegistry(
    name='cooktop',
    capabilities=_build([
        *ignored.IGNORED,
        cooktop.COOKTOP_POWER,
        cooktop.COOKTOP_MODE,
        cooktop.COOKTOP_CONNECTED,
        cooktop.PAIRED_HOOD_STATUS,
        common.FIRMWARE_UPDATE,
    ]),
)

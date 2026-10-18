"""Read-only registry for the TP6X dryer bridge."""

from ..capabilities import common, dryer, ignored, legacy_dryer
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="dryer",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *(legacy_dryer.read_only(capability) for capability in common.UNIVERSAL),
            legacy_dryer.POWER,
            legacy_dryer.OPERATIONAL_STATE,
            legacy_dryer.SETTINGS,
            legacy_dryer.COURSE,
            dryer.DRYER_DIAGNOSIS,
        ]
    ),
)

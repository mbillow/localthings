"""Refrigerator device registry."""
from ..capabilities import common, fridge
from ._base import DeviceRegistry, _build

# Placeholder: Task 4 will expand with full capability list.
# For now, include common capabilities to verify the import chain.
REGISTRY = DeviceRegistry(
    name='refrigerator',
    capabilities=_build([
        common.POWER,
        common.KIDS_LOCK,
        common.REMOTE_CONTROL,
    ]),
)

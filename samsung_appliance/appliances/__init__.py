"""Appliance descriptors registry.

Adding a new appliance class:
  1. Write `appliances/<class>.py` with an ApplianceDescriptor named
     after the class (uppercase, e.g. OVEN).
  2. Add it to DESCRIPTORS below.
  3. Set DEVICE_CLASS=<class> in the per-device .env.

main.py imports get_descriptor(name) to look up the descriptor at
startup; the bridge itself stays class-agnostic.
"""
from .base import ApplianceDescriptor
from .dishwasher import DISHWASHER
from .dryer import DRYER
from .oven import OVEN
from .refrigerator import REFRIGERATOR


DESCRIPTORS: dict[str, ApplianceDescriptor] = {
    DISHWASHER.name:   DISHWASHER,
    DRYER.name:        DRYER,
    OVEN.name:         OVEN,
    REFRIGERATOR.name: REFRIGERATOR,
}


def get_descriptor(name: str) -> ApplianceDescriptor:
    try:
        return DESCRIPTORS[name]
    except KeyError:
        raise ValueError(
            f"unknown DEVICE_CLASS={name!r}; "
            f"available: {sorted(DESCRIPTORS)}") from None


__all__ = ['ApplianceDescriptor', 'DESCRIPTORS', 'get_descriptor']

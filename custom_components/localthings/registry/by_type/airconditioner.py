"""Air-conditioner device registry (Samsung ARTIK051_PRAC-class, issue #17).

The first device whose core controls surface as a single composite HA `climate`
entity (see capabilities/airconditioner.py and climate.py). Power/mode/temp/wind
are consumed by that entity rather than exposed as separate switches/selects, so
this registry deliberately does NOT include the common POWER caps -- on/off is
the climate entity's HVACMode.OFF / TURN_ON/OFF.

Reuses common.ALARMS + common.ENERGY_METER, fridge.FIRMWARE_UPDATE (as every
registry does), and dishwasher.DIAGNOSIS for /diagnosis/vs/0.
"""
from ..capabilities import airconditioner, common, dishwasher, fridge, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='airconditioner',
    capabilities=_build([
        *ignored.IGNORED,
        common.ALARMS,
        common.ENERGY_METER,
        fridge.FIRMWARE_UPDATE,
        dishwasher.DIAGNOSIS,
        airconditioner.CLIMATE,
        airconditioner.AIR_PURIFY,
        airconditioner.AUTO_CLEAN,
        airconditioner.AIR_FILTER,
        airconditioner.DISPLAY_LIGHT,
        *airconditioner.COVERAGE,
    ]),
)

"""Air-conditioner device registry (Samsung ARTIK051_PRAC-class, issue #17).

The first device whose core controls surface as a single composite HA `climate`
entity (see capabilities/airconditioner.py and climate.py). Power/mode/temp/wind
are consumed by that entity rather than exposed as separate switches/selects, so
this registry includes *common.UNIVERSAL but deliberately NOT common.POWER --
on/off is the climate entity's HVACMode.OFF / TURN_ON/OFF, and /power/0 +
/power/vs/0 are already pre-claimed (no-entity) by airconditioner.COVERAGE via
CLIMATE_CONSUMED_HREFS. A real POWER_GENERIC/POWER_VS_FALLBACK cap on either
href alongside that bare claim would make _build() raise (see its docstring).

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0.
"""
from ..capabilities import airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='airconditioner',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        dishwasher.DIAGNOSIS,
        airconditioner.CLIMATE,
        airconditioner.AIR_PURIFY,
        airconditioner.AUTO_CLEAN,
        airconditioner.AIR_FILTER,
        airconditioner.DISPLAY_LIGHT,
        airconditioner.MUTE_ONCE,
        airconditioner.CURRENT_LIMIT,
        *airconditioner.COVERAGE,
    ]),
)

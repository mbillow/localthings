"""Dehumidifier device registry (Samsung TP1X_DA_AC_DHM-class, issue #88).

Shares the DA_AC_ board family with airconditioner.py (power, air filter,
auto-clean, mute-once all use the identical resource shapes), so those three
Capability objects are reused directly rather than duplicated.
"""
from ..capabilities import airconditioner, common, dehumidifier, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='dehumidifier',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        dehumidifier.MODE,
        dehumidifier.HUMIDITY,
        airconditioner.AUTO_CLEAN,
        airconditioner.AIR_FILTER,
        airconditioner.MUTE_ONCE,
        *dehumidifier.COVERAGE,
    ]),
)

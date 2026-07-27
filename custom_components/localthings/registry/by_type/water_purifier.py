"""Water-purifier device registry (Samsung TP2X_WATERPURIFIER-class, issue #90).
"""
from ..capabilities import common, ignored, water_purifier
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='water_purifier',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        common.WATER_FILTER,
        water_purifier.DISPENSE,
        water_purifier.STATUS,
        water_purifier.FAVORITE_CAPACITY,
        water_purifier.FAVORITE_HOTWATER,
        water_purifier.COFFEE,
        water_purifier.LOCK,
        *water_purifier.COVERAGE,
    ]),
)

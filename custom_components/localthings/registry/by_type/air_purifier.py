"""Air-purifier device registry (Samsung ARTIK051_TVTL-class, issue #56).

Reports no oneUiVersion; resolved via for_device_by_model's '_TVTL_' modelNum
token (see registry.py). Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0
(identical field/write contract).

/humidity/0 and /humidity/vs/0 are empty {} on both dumps this family has
been verified against -- ignored here rather than globally since those hrefs
collide with fridge/AC schemas elsewhere (see ignored.py's module docstring).
"""
from ..capabilities import air_purifier, common, dishwasher, ignored
from ..capability import Capability
from ._base import DeviceRegistry, _build

_HUMIDITY_EMPTY = [
    Capability(href='/humidity/0'),
    Capability(href='/humidity/vs/0'),
]

REGISTRY = DeviceRegistry(
    name='air_purifier',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        dishwasher.DIAGNOSIS,
        air_purifier.AIR_QUALITY,
        air_purifier.FILTER,
        air_purifier.DEVICE_ACTIVE,
        air_purifier.AIRFLOW_GENERIC,
        air_purifier.AIRFLOW_VS_FALLBACK,
        air_purifier.MODE,
        *_HUMIDITY_EMPTY,
    ]),
)

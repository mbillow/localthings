"""Air-purifier device registry (Samsung ARTIK051_TVTL-class, issue #56).

Reports no oneUiVersion; resolved via for_device_by_model's '_TVTL_' modelNum
token (see registry.py). Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0
(identical field/write contract).
"""
from ..capabilities import air_purifier, airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='air_purifier',
    capabilities=_build([
        # Drop the global /wirelessinfo ignore -- this family exposes the SSID
        # (air_purifier.WIRELESS below) instead.
        *[c for c in ignored.IGNORED if c.href != '/wirelessinfo/vs/0'],
        *common.UNIVERSAL,
        # Power switch only on fan-less boards (TVTL); on AVT the fan owns on/off.
        air_purifier.POWER_SWITCH,
        air_purifier.POWER_SWITCH_VS,
        dishwasher.DIAGNOSIS,
        air_purifier.AIR_QUALITY,
        air_purifier.FILTER,
        air_purifier.DEVICE_ACTIVE,
        air_purifier.AIRFLOW_GENERIC,
        air_purifier.AIRFLOW_VS_FALLBACK,
        air_purifier.MODE,
        # AVT-WW-TP1 sub-family additions (bind only on that board's hrefs).
        air_purifier.WIND_STRENGTH,
        air_purifier.HEPA_FILTER,
        air_purifier.AIR_LEVEL_CHECK,
        air_purifier.WIRELESS,
        airconditioner.MUTE_ONCE,
        *air_purifier.COVERAGE,
        *air_purifier.AVT_COVERAGE,
    ]),
)

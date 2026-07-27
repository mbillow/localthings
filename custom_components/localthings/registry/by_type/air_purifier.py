"""Air-purifier device registry -- two board families share it.

Neither reports a oneUiVersion, so both resolve through for_device_by_model
(see registry.py):
  ARTIK051_TVTL-class (issue #56)  -- matched on the '_TVTL_' modelNum token.
  AVT-WW-TP1-class    (issue #84)  -- matched on the 'AVT-' prefix.

They overlap on the sensors/humidity/device-active/display-light capabilities
and diverge on fan speed, filter, and the AI Purify engine. Every capability
below binds only when its own href is present in the dump, so listing both
families' capabilities in one registry is safe -- see capabilities/
air_purifier.py for which href belongs to which family.

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0 (identical field/write
contract; TVTL only -- AVT doesn't expose that href).
"""
from ..capabilities import air_purifier, airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='air_purifier',
    capabilities=_build([
        *ignored.IGNORED,
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
        airconditioner.MUTE_ONCE,
        *air_purifier.COVERAGE,
        *air_purifier.AVT_COVERAGE,
    ]),
)

"""Air-conditioner device registry (Samsung ARTIK051_PRAC-class, issue #17).

The first device whose core controls surface as a single composite HA `climate`
entity (see capabilities/airconditioner.py and climate.py). Power/mode/temp/wind
are consumed by that entity rather than exposed as separate switches/selects, so
this registry includes *common.UNIVERSAL but deliberately NOT common.POWER --
on/off is the climate entity's HVACMode.OFF / TURN_ON/OFF. See common.POWER's
own comment in capabilities/common.py for why it's excluded.

common.ENERGY_METER itself is also excluded from UNIVERSAL here, replaced by
the ENERGY_METER_GENERIC/ENERGY_METER_LEGACY pair -- the legacy ARTIK051 board
generation (issue #193) reports cumulativePower in a different unit than
every other AC family, so this registry needs two mutually-exclusive variants
of that one capability instead of the single shared one every other registry
uses unconditionally.

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0.
"""

from ..capabilities import air_purifier, airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="airconditioner",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *[c for c in common.UNIVERSAL if c is not common.ENERGY_METER],
            airconditioner.ENERGY_METER_GENERIC,
            airconditioner.ENERGY_METER_LEGACY,
            dishwasher.DIAGNOSIS,
            airconditioner.CLIMATE,
            airconditioner.AIR_PURIFY,
            airconditioner.AUTO_CLEAN,
            airconditioner.AIR_FILTER,
            airconditioner.AIR_FILTER_PM1,
            airconditioner.AIR_QUALITY,
            airconditioner.DISPLAY_LIGHT,
            airconditioner.UV_LED,
            airconditioner.VENTILATION_ALARM,
            airconditioner.MUTE_ONCE,
            airconditioner.CURRENT_LIMIT,
            airconditioner.ANOMALY_LOAD,
            airconditioner.ABSENCE_POWER_SAVING,
            airconditioner.MOTION_DETECT_WIND,
            airconditioner.CURRENT_TEMPERATURE,
            airconditioner.CURRENT_TEMPERATURE_VS,
            airconditioner.HUMIDITY,
            # TP1X_DA-AC-FAC-class (issue #319): shares its /display/vs/0,
            # /settings/sound/output/vs/0 and /settings/sound/volume/vs/0
            # shape with the sibling TP1X_DA-AC-AIR board in air_purifier.py.
            air_purifier.DISPLAY,
            air_purifier.SOUND_OUTPUT,
            air_purifier.SOUND_VOLUME,
            airconditioner.SOUND_MODE,
            airconditioner.ABSENCE_CLEAN,
            airconditioner.MDS_ABSENCE_CLEAN,
            airconditioner.ENERGY_SAVING,
            airconditioner.EDGE_LIGHTING,
            airconditioner.LIGHT_STATEFUL,
            # System Fresh Air Ventilator (PR #316, ACA-KR-TP2-21-AN9000):
            # WINDFREE/WINDSLEEP are this device's own hrefs; HEPA_FILTER/
            # DEVICE_ACTIVE reuse air_purifier.py's identical shapes.
            # AIR_LEVEL_CHECK is not this-device-specific -- see its
            # removal from _AC_IGNORED above.
            airconditioner.WINDFREE,
            airconditioner.WINDSLEEP,
            air_purifier.HEPA_FILTER,
            air_purifier.DEVICE_ACTIVE,
            air_purifier.AIR_LEVEL_CHECK,
            # TP1X_DA-AC-DUCT slim duct (issue #501).
            airconditioner.AUTO_CHANGEOVER,
            airconditioner.DUAL_SETPOINT,
            *airconditioner.COVERAGE,
        ]
    ),
)

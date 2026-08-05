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

common.ALARMS is swapped the same way, for ALARMS_WITH_MESSAGE -- the alarm
code sensor is shared with every other family, and the added sensor that turns
an air-conditioner error code into its message is not.

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0.
"""

from ..capabilities import airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="airconditioner",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *[
                c
                for c in common.UNIVERSAL
                if c is not common.ENERGY_METER and c is not common.ALARMS
            ],
            airconditioner.ALARMS_WITH_MESSAGE,
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
            *airconditioner.COVERAGE,
        ]
    ),
)

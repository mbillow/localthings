"""AirDresser device registry (issue #162).

Reuses washer/dryer/dishwasher's shared laundry surface -- job-beginning
status, operational state, diagnosis, and (issue #208) the Boolean buzzer
switch -- /buzzersound/vs/0's rep on this board is the plain
{setBuzzerSound, supportedBuzzerSound} shape laundry.BUZZER_SOUND already
expects, no AirDresser-specific wiring needed. air_dresser.py holds the two
pieces specific to this device type: a minimal wrinkle-prevent-only
/washer/vs/0 capability, and the course select's own translation key.
"""

from ..capabilities import air_dresser, common, dishwasher, ignored, laundry, operational
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="air_dresser",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            air_dresser.AIR_DRESSER_SETTINGS,
            air_dresser.AIR_DRESSER_COURSE,
            air_dresser.AIR_DRESSER_SANITIZE,
            laundry.BUZZER_SOUND,
            laundry.JOB_BEGINNING_STATUS,
            operational.OPERATIONAL_STATE,
            dishwasher.DIAGNOSIS,
        ]
    ),
)

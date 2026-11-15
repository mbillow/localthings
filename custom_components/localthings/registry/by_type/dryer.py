"""Dryer device registry.

Note: dryer devices use port 49155 (not 49154). The config flow handles
this automatically via port probing.

The power/kids-lock/remote-control, buzzer, energy-meter, job-status, and
cycle-select capabilities are the shared laundry ones (laundry.py), the same
objects the washer registry uses -- washer and dryer expose the same
DA_WM_-family surface, so they stay consistent instead of each carrying a
bespoke variant.
"""

from dataclasses import replace

from ..capabilities import common, dryer, ignored, laundry, operational
from ..entities import BinarySensorDesc, ButtonDesc, SelectDesc, SensorDesc, SwitchDesc
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="dryer",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            operational.OPERATIONAL_STATE,
            laundry.DOOR_LED,
            laundry.SOUND_MODE,
            laundry.BUZZER_SOUND,
            laundry.JOB_BEGINNING_STATUS,
            dryer.DRYER_SETTINGS,
            dryer.DRYER_COURSE,
            dryer.DRYER_DIAGNOSIS,
        ]
    ),
)


def _flexdry_description(desc):
    """Expose only the confirmed FlexDry controls."""
    if isinstance(desc, (SensorDesc, BinarySensorDesc)):
        return desc
    if isinstance(desc, SelectDesc) and desc.key in {"cycle", "dry_level", "dry_time"}:
        return desc
    if isinstance(desc, SwitchDesc) and desc.key in {"wrinkle_prevent", "damp_alert"}:
        return desc
    if isinstance(desc, ButtonDesc) and desc.key in {"start", "pause", "stop"}:
        return desc
    return None


def _flexdry_registry() -> DeviceRegistry:
    capabilities = []
    for caps in REGISTRY.capabilities.values():
        for cap in caps:
            entities = tuple(
                approved
                for desc in cap.entities
                if (approved := _flexdry_description(desc)) is not None
            )
            if cap is dryer.DRYER_SETTINGS:
                temperature = "x.com.samsung.da.waterTemperature"
                supported = "x.com.samsung.da.supportedWaterTemperature"
                entities += (
                    SelectDesc(
                        key="dryer_temperature",
                        field=temperature,
                        icon="mdi:thermometer",
                        entity_category="config",
                        options=laundry.course_narrowed_options(
                            laundry.OPTION_KIND_WATER_TEMPERATURE, temperature, supported
                        ),
                        exists_fn=lambda rep, resources: (
                            bool(rep.get("x.com.samsung.da.supportedWaterTemperature"))
                            and resources.get("/information/vs/0", {}).get(
                                "x.com.samsung.da.description"
                            )
                            == "TP6X_DRYER_DRUM"
                        ),
                        write_fn=dryer._setting_write(temperature),
                    ),
                )
            if entities or not cap.entities:
                capabilities.append(replace(cap, entities=entities))
    return DeviceRegistry(name="flexdry", capabilities=_build(capabilities))


FLEXDRY_REGISTRY = _flexdry_registry()

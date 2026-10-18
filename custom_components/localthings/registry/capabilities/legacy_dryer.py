"""Read-only TP6X dryer capabilities; command behavior is not yet measured."""

from dataclasses import replace

from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc
from . import common, dryer, operational


def _reported_flag(value: object, enabled: str, disabled: str) -> bool | None:
    """Keep missing or unexpected readings unknown rather than reporting off."""
    if value == enabled:
        return True
    if value == disabled:
        return False
    return None


def read_only(capability: Capability) -> Capability:
    return replace(
        capability,
        entities=tuple(
            desc for desc in capability.entities if isinstance(desc, (SensorDesc, BinarySensorDesc))
        ),
    )


OPERATIONAL_STATE = read_only(operational.OPERATIONAL_STATE)
POWER = replace(
    common.POWER_VS_FALLBACK,
    entities=tuple(
        replace(desc, exists_fn=None, value_fn=lambda value: _reported_flag(value, "On", "Off"))
        for desc in common.POWER_VS_FALLBACK.entities
        if isinstance(desc, BinarySensorDesc)
    ),
)

SETTINGS = replace(
    dryer.DRYER_SETTINGS,
    entities=(
        *read_only(dryer.DRYER_SETTINGS).entities,
        SensorDesc(
            key="legacy_dryer_dry_level",
            field="x.com.samsung.da.dryLevel",
            icon="mdi:water-percent",
        ),
        SensorDesc(
            key="legacy_dryer_dry_time",
            field="x.com.samsung.da.dryTime",
            icon="mdi:timer",
        ),
        BinarySensorDesc(
            key="legacy_dryer_wrinkle_prevent",
            field="x.com.samsung.da.wrinklePrevent",
            icon="mdi:iron-outline",
            value_fn=lambda value: _reported_flag(value, "On", "Off"),
        ),
    ),
)

COURSE = Capability(
    href="/course/vs/0",
    poll_tier="warm",
    entities=(
        # No course table is reported; keep the code rather than guessing its name.
        SensorDesc(
            key="legacy_dryer_cycle_code",
            icon="mdi:tumble-dryer",
            rep_fn=lambda rep: common.option_value(rep.get("x.com.samsung.da.options"), "Course"),
        ),
        BinarySensorDesc(
            key="legacy_dryer_damp_alert",
            icon="mdi:bell-ring-outline",
            exists_fn=lambda rep, resources: (
                common.option_value(rep.get("x.com.samsung.da.options"), "MixedLoadBell")
                is not None
            ),
            rep_fn=lambda rep: _reported_flag(
                common.option_value(rep.get("x.com.samsung.da.options"), "MixedLoadBell"),
                "Enable",
                "Disable",
            ),
        ),
    ),
)

"""Divider-aware labels without changing oven devices or command routes."""

from .registry.capabilities import cook
from .registry.subdevices import MAIN, Subdevice, canonical_view

CAVITY_KEYS = frozenset(
    {
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time_hours",
        "cook_time_minutes",
        "cook_time",
        "machine_state",
        "cycle_active",
        "progress_percentage",
        "operation_time_minutes",
        "finish_time",
        "oven_state",
        "current_temp_c",
        "alarm_code",
        "diagnosis_status",
    }
)


def main_cavity_divider(resources: dict[str, dict], subdevices: list[Subdevice]) -> str | None:
    """Require one declared Lower cavity and its divider resource, not a model name."""
    if len(subdevices) != 1:
        return None
    lower = subdevices[0]
    main_view = canonical_view(MAIN, resources, subdevices)
    lower_view = canonical_view(lower, resources, subdevices)
    divider = lower.to_actual(cook.CONNECTED_HREF)
    if (
        cook.cavity(main_view) != "Lower"
        and cook.cavity(lower_view) == "Lower"
        and divider in resources
        and all(
            cook.CAVITY_HREF in view and cook.MODE_HREF in view for view in (main_view, lower_view)
        )
    ):
        return divider
    return None

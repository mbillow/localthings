"""Display labels for a wall oven with a reported divider cavity."""

from .subdevices import MAIN, Subdevice

CAVITY_KEYS = frozenset(
    {
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time",
        "machine_state",
        "cycle_active",
        "progress_percentage",
        "operation_time_minutes",
        "finish_time",
        "oven_state",
        "current_temp_c",
    }
)

_PREFIX = "x.com.samsung.da."


def cavity_divider(
    resources: dict[str, dict], subdevice: Subdevice, subdevices: list[Subdevice]
) -> str | None:
    """Find this oven's enabled lower cavity without assuming a slot or model."""
    info = resources.get(subdevice.to_actual("/information/vs/0")) or {}
    description = info.get(_PREFIX + "description")
    if (
        info.get(_PREFIX + "deviceSubType") != "WallOven"
        or not isinstance(description, str)
        or not description
        or description.endswith("_DIV")
    ):
        return None
    # The lower record extends the upper description with _DIV (#572).
    # Only discovered, enabled siblings participate; ambiguous pairs stay unchanged.
    matches = []
    for sibling in (MAIN, *subdevices):
        if sibling == subdevice:
            continue
        lower = resources.get(sibling.to_actual("/information/vs/0")) or {}
        divider = sibling.to_actual("/connected/vs/0")
        if (
            lower.get(_PREFIX + "deviceSubType") == "WallOven"
            and lower.get(_PREFIX + "description") == description + "_DIV"
        ):
            matches.append(divider)
    return matches[0] if len(matches) == 1 else None

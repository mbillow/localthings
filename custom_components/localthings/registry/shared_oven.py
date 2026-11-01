"""Shared physical sensors on the NV51K7770S wall oven's cavity pair."""

from .capabilities import oven
from .subdevices import Subdevice

_PREFIX = "x.com.samsung.da."


def shared_sensor_view(resources: dict[str, dict], subdevices: list[Subdevice]) -> dict[str, dict]:
    """Overlay display readings without changing raw state or write payloads."""
    # This pair shares one door and tank. The same board also appears in
    # the NW9300MD combo (#681), whose microwave and oven must stay separate.
    if [(s.kind, s.key) for s in subdevices] != [("indexed", "1")]:
        return resources
    main = resources.get("/information/vs/0") or {}
    lower = resources.get("/information/vs/1") or {}
    if (
        any(
            info.get(_PREFIX + "deviceSubType") != "WallOven"
            or str(info.get(_PREFIX + "modelNum", "")).split("|", 1)[0] != "LCD_OV_WALL_16K"
            for info in (main, lower)
        )
        or lower.get(_PREFIX + "description") != "LCD_OV_WALL_16K_DIV"
    ):
        return resources

    divider = (resources.get("/connected/vs/1") or {}).get(_PREFIX + "connected")
    view = {
        **resources,
        "/information/vs/0": {**main, oven.SHARED_DIVIDER: {"On": True, "Off": False}.get(divider)},
    }
    lower_door = resources.get("/doors/vs/1") or {}
    items = (resources.get("/doors/vs/0") or {}).get(_PREFIX + "items")
    if (
        lower_door.get(_PREFIX + "items") == []
        and isinstance(items, list)
        and len(items) == 1
        and isinstance(items[0], dict)
        and items[0].get(_PREFIX + "id") == "0"
        and items[0].get(_PREFIX + "openState") in ("Open", "Close")
    ):
        view["/doors/vs/1"] = {**lower_door, _PREFIX + "items": items}

    options = (resources.get("/mode/vs/1") or {}).get(_PREFIX + "options")
    if options is not None and not isinstance(options, list):
        return view
    own_switch = any(
        oven._option_value(options, name) is not None
        for name in ("waterTankInSwitch", "waterTankOutSwitch")
    )
    main_mode = resources.get("/mode/vs/0") or {}
    if not own_switch and oven._has_reservoir(main_mode, resources):
        # Copy only the position; copying options could leak Upper settings
        # into a Lower control write. Even a partial Lower reading takes precedence.
        view["/information/vs/1"] = {
            **lower,
            oven.SHARED_RESERVOIR: oven._reservoir_open(main_mode.get(_PREFIX + "options")),
        }
    return view

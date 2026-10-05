"""Starting an oven or microwave cook, validated against the board's own
`modeSpec` (issue #473).

A cook starts from one Collection write to `/device/0` carrying the mode,
the setpoint and the cook time with `state: "Run"` -- measured on an
NV7000BS wall oven and an NE63A6111SS range. Sent as separate writes from
`Ready`, the same values are accepted and discarded, so the mode, setpoint
and cook-time entities hold what is chosen while the oven is idle
(`HeldCook`) and the start sends it all at once. See
docs/investigations/oven-cycle-start.md.

`/mode/vs/0`'s modeSpec declares, per mode, whether a remote may start it
(`control`), and its temperature, time and power limits. A board without
one is offered its own live modes instead (_specless_specs).
What a cavity can run right now is its own live `supportedModes`, which
is narrower: the NE9801T's modeSpec lists all 15 modes of both cavities
while its upper cavity supports only the three Upper ones (#324).

A dual-cavity range is two subdevices, each with its own collection and
its own mode, temperatures and operational state; only the upper
cavity's `/mode/vs/0` carries the modeSpec (share_mode_spec). Each cavity
starts from its own collection.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .common import int_or_none

MODE_HREF = "/mode/vs/0"
TEMPERATURES_HREF = "/temperatures/vs/0"
OPERATION_HREF = "/operational/state/vs/0"
CAVITY_HREF = "/oven/vs/0"

_MODES = "x.com.samsung.da.modes"
_MODE_SPEC = "x.com.samsung.da.modeSpec"
_SUPPORTED_MODES = "x.com.samsung.da.supportedModes"
_ITEMS = "x.com.samsung.da.items"
_DESIRED = "x.com.samsung.da.desired"
_STATE = "x.com.samsung.da.state"
_OPERATION_TIME = "x.com.samsung.da.operationTime"

START_CONTROL = "Start&Setting"
IDLE_STATE = "Ready"

_UNIT_KEYS = {"Celsius": "C", "Fahrenheit": "F"}


class CookStartError(Exception):
    """A start the board's own declaration rules out. `key` is an
    `exceptions` translation key; `placeholders` fill its message."""

    def __init__(self, key: str, **placeholders: Any) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = {k: str(v) for k, v in placeholders.items()}


@dataclass(frozen=True)
class TempSpec:
    minimum: int
    maximum: int
    default: int | None
    # None when the mode declares no interval; callers fall back to the
    # temperatures item's own increment.
    step: int | None
    # A fixed list (Keep Warm's single value, Broil's Hi/Lo codes) rather
    # than a range.
    choices: tuple[int, ...] = ()


@dataclass(frozen=True)
class ModeSpec:
    mode: str
    control: str
    temps: dict[str, TempSpec] = field(default_factory=dict)
    # Seconds.
    time_min: int | None = None
    time_max: int | None = None
    time_default: int | None = None
    # A start may leave the cook time out.
    time_optional: bool = False

    @property
    def startable(self) -> bool:
        return self.control == START_CONTROL


def parse_hms(value: Any) -> int | None:
    """'HH:MM:SS' -> seconds; None for 'NotSupported' or anything else."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def format_hms(seconds: int) -> str:
    """Seconds -> 'HH:MM:SS'. The hour is zero-padded: `0:10:00` ran a
    ten-minute cook for about 609 minutes on hardware."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _temp_spec(entry: dict, unit: str) -> TempSpec | None:
    lo = int_or_none(entry.get(f"tempMin{unit}"))
    hi = int_or_none(entry.get(f"tempMax{unit}"))
    if lo is None or hi is None:
        return None
    choices = tuple(
        c for c in (int_or_none(v) for v in entry.get(f"tempListData{unit}") or ()) if c is not None
    )
    interval = int_or_none(entry.get(f"tempInterval{unit}"))
    return TempSpec(
        minimum=min(lo, hi),
        maximum=max(lo, hi),
        default=int_or_none(entry.get(f"tempDefault{unit}")),
        step=interval if interval and interval > 0 else None,
        choices=choices,
    )


# Modes that no board publishing a modeSpec declares Start&Setting, plus
# maintenance programs: not offered on a board that publishes none. Name
# families stand in for their variants (BroilS, Easycook3, the Speed*
# microwave combinations).
_NEVER_STARTABLE = frozenset(
    {
        "BreadProof",
        "Defrost",
        "Descale",
        "Drain",
        "NoOperation",
        "PyroFree",
        "SelfClean",
        "SteamClean",
    }
)
_NEVER_STARTABLE_FAMILIES = ("Autocook", "Broil", "Easycook", "HOMECARE", "Speed", "Toast")


def _specless_startable(mode: str) -> bool:
    return (
        mode not in _NEVER_STARTABLE
        and "MicroWave" not in mode
        and not mode.removeprefix("Upper")
        .removeprefix("Lower")
        .startswith(_NEVER_STARTABLE_FAMILIES)
    )


def _specless_specs(resources: dict) -> dict[str, ModeSpec]:
    """A board with no modeSpec starts the same way (the NW9000KD started
    Bake from Ready, #300), so its own live modes are offered, bounded by the
    oven's static setpoint range when it reports a temperature."""
    from .oven import (
        SETPOINT_MAX_C,
        SETPOINT_MAX_F,
        SETPOINT_MIN_C,
        SETPOINT_MIN_F,
        SETPOINT_STEP_C,
        SETPOINT_STEP_F,
    )

    temps = (
        {
            "C": TempSpec(SETPOINT_MIN_C, SETPOINT_MAX_C, None, SETPOINT_STEP_C),
            "F": TempSpec(SETPOINT_MIN_F, SETPOINT_MAX_F, None, SETPOINT_STEP_F),
        }
        if device_unit(resources)
        else {}
    )
    live = (resources.get(MODE_HREF) or {}).get(_SUPPORTED_MODES)
    return {
        mode: ModeSpec(
            mode=mode,
            control=START_CONTROL,
            temps=temps,
            time_max=parse_hms("23:59:00"),
            time_optional=True,
        )
        for mode in (live if isinstance(live, list) else ())
        if isinstance(mode, str) and _specless_startable(mode)
    }


def mode_specs(resources: dict) -> dict[str, ModeSpec]:
    """{mode: ModeSpec} from `/mode/vs/0`'s modeSpec, which arrives as a
    JSON string; a board reporting none gets _specless_specs."""
    raw = (resources.get(MODE_HREF) or {}).get(_MODE_SPEC)
    if raw is None:
        return _specless_specs(resources)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    specs = {}
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict) or not isinstance(entry.get("mode"), str):
            continue
        temps = {u: t for u in ("C", "F") if (t := _temp_spec(entry, u)) is not None}
        specs[entry["mode"]] = ModeSpec(
            mode=entry["mode"],
            control=str(entry.get("control")),
            temps=temps,
            time_min=parse_hms(entry.get("timeMin")),
            time_max=parse_hms(entry.get("timeMax")),
            time_default=parse_hms(entry.get("timeDefault")),
        )
    return specs


def share_mode_spec(resources: dict, mode_hrefs: list[str]) -> dict:
    """`resources` with the upper cavity's modeSpec copied onto each of
    `mode_hrefs` (a sibling cavity's actual `/mode` href) that has none.
    It is one board-wide list -- the upper cavity's names the Lower modes
    too -- so a lower cavity is checked against it rather than getting no
    start. Returns `resources` itself when there is nothing to share."""
    spec = (resources.get(MODE_HREF) or {}).get(_MODE_SPEC)
    targets = [h for h in mode_hrefs if h in resources and _MODE_SPEC not in resources[h]]
    if spec is None or not targets:
        return resources
    shared = dict(resources)
    for href in targets:
        shared[href] = {**resources[href], _MODE_SPEC: spec}
    return shared


def startable_modes(resources: dict) -> list[str]:
    """Modes this cavity can start now: declared startable, and in its own
    live supportedModes when it reports one."""
    live = (resources.get(MODE_HREF) or {}).get(_SUPPORTED_MODES)
    return [
        name
        for name, spec in mode_specs(resources).items()
        if spec.startable and (not isinstance(live, list) or name in live)
    ]


def can_start(rep: dict, resources: dict) -> bool:
    """exists_fn for the start button."""
    return bool(startable_modes(resources))


def _temp_item(resources: dict) -> dict:
    items = (resources.get(TEMPERATURES_HREF) or {}).get(_ITEMS) or []
    return items[0] if items and isinstance(items[0], dict) else {}


def device_unit(resources: dict) -> str | None:
    """The oven's own unit spelling ('Celsius'/'Fahrenheit'), as the
    temperatures item reports and takes it back."""
    unit = _temp_item(resources).get("x.com.samsung.da.unit")
    return unit if unit in _UNIT_KEYS else None


def current_mode(resources: dict) -> str | None:
    modes = (resources.get(MODE_HREF) or {}).get(_MODES)
    return modes[0] if isinstance(modes, list) and modes else None


def operation_ready(resources: dict) -> bool:
    return (resources.get(OPERATION_HREF) or {}).get(_STATE) == IDLE_STATE


def is_idle(resources: dict) -> bool:
    """Nothing cooking. `Ready` on the operational state is not enough: a
    mode with no cook time runs under it, as #473's dump shows (Keep Warm
    at 175F, operational state and cavity both `Ready`). Every idle dump
    in the corpus reads `NoOperation`."""
    cavity = (resources.get(CAVITY_HREF) or {}).get(_STATE)
    return (
        operation_ready(resources)
        and cavity in (None, IDLE_STATE)
        and current_mode(resources) in (None, "NoOperation")
    )


def temp_bounds(resources: dict, mode: str | None = None) -> tuple[int, int, int] | None:
    """(min, max, step) for `mode` (default: the current one) in the oven's
    own unit, or None when the mode declares no temperature range. A fixed
    list is not a range: Broil's is two codes, 61441/61442, not degrees."""
    spec = mode_specs(resources).get(mode or current_mode(resources) or "")
    unit = _UNIT_KEYS.get(device_unit(resources) or "")
    temp = spec.temps.get(unit) if spec and unit else None
    if temp is None or temp.choices:
        return None
    return temp.minimum, temp.maximum, _step(temp, resources)


def _step(temp: TempSpec, resources: dict) -> int:
    if temp.step:
        return temp.step
    increment = int_or_none(_temp_item(resources).get("x.com.samsung.da.increment"))
    return increment if increment and increment > 0 else 1


# ---------------------------------------------------------------------------
# The start
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CookPlan:
    mode: str
    temperature: int | None
    unit: str | None
    duration: int | None

    def batch(self, to_actual: Callable[[str], str] | None = None) -> list[dict]:
        """The Collection payload for the cavity's own collection, with
        `to_actual` spelling each element's href as that cavity's reads and
        writes already do (`/mode/vs/1` for a lower cavity).

        The bare `/devices/0` marker is not required on either board
        measured, but both measured payloads carried it, so the upper
        cavity keeps it. A lower cavity leaves it out rather than guess
        its spelling."""
        actual = to_actual or (lambda href: href)
        elements: list[dict] = [{"href": "/devices/0"}] if to_actual is None else []
        elements.append({"href": actual(MODE_HREF), "rep": {_MODES: [self.mode]}})
        if self.temperature is not None:
            elements.append(
                {
                    "href": actual(TEMPERATURES_HREF),
                    "rep": {
                        _ITEMS: [
                            {
                                "x.com.samsung.da.id": "0",
                                _DESIRED: str(self.temperature),
                                "x.com.samsung.da.unit": self.unit,
                            }
                        ]
                    },
                }
            )
        operation: dict[str, str] = {}
        if self.duration is not None:
            operation[_OPERATION_TIME] = format_hms(self.duration)
        operation[_STATE] = "Run"
        elements.append({"href": actual(OPERATION_HREF), "rep": operation})
        return elements


def plan_start(
    resources: dict,
    mode: str | None = None,
    temperature: float | None = None,
    duration: int | None = None,
    *,
    complete: bool = True,
) -> CookPlan:
    """Check a start against the board's declaration and fill what was
    left out with the mode's own defaults. `temperature` is in the oven's
    own unit and is rounded to the mode's step; `duration` is seconds.
    Raises CookStartError naming what the board allows.

    `complete=False` checks only what is given, for a value held before
    the rest are chosen: a mode with no default time is not refused for
    lacking one yet."""
    specs = mode_specs(resources)
    if not any(spec.startable for spec in specs.values()):
        raise CookStartError("cook_start_not_supported")
    startable = startable_modes(resources)
    if not is_idle(resources):
        raise CookStartError("cook_start_not_idle")

    if mode is None:
        default = (resources.get(MODE_HREF) or {}).get("x.com.samsung.da.defaultMode")
        if default not in startable:
            raise CookStartError("cook_mode_required", startable=", ".join(startable))
        mode = default
    spec = specs.get(mode)
    if spec is None or not spec.startable:
        raise CookStartError("cook_mode_not_startable", mode=mode, startable=", ".join(startable))
    if mode not in startable:
        # Declared startable, but not a mode this cavity offers now: the
        # other cavity's, or a whole-oven mode while the divider is in.
        raise CookStartError("cook_mode_unavailable", mode=mode, startable=", ".join(startable))

    unit = device_unit(resources)
    temp = spec.temps.get(_UNIT_KEYS.get(unit or "", ""))
    if temp is None:
        if temperature is not None:
            raise CookStartError("cook_temperature_not_supported", mode=mode)
        target = None
    else:
        target = _check_temperature(temp, temperature, mode, unit, resources, complete)

    if spec.time_max is None:
        if duration is not None:
            raise CookStartError("cook_duration_not_supported", mode=mode)
        seconds = None
    else:
        seconds = spec.time_default if duration is None else int(duration)
        lo = spec.time_min or 0
        if seconds is None and (not complete or spec.time_optional):
            pass
        elif seconds is None or not lo <= seconds <= spec.time_max:
            raise CookStartError(
                "cook_duration_out_of_range",
                mode=mode,
                min=format_hms(lo),
                max=format_hms(spec.time_max),
            )
    return CookPlan(mode=mode, temperature=target, unit=unit, duration=seconds)


def _check_temperature(temp, temperature, mode, unit, resources, complete=True) -> int | None:
    symbol = "°C" if unit == "Celsius" else "°F"
    if temperature is None:
        if temp.default is None and not complete:
            return None
        if temp.default is None:
            raise CookStartError("cook_temperature_required", mode=mode)
        return temp.default
    if temp.choices:
        value = round(temperature)
        if value not in temp.choices:
            raise CookStartError(
                "cook_temperature_not_a_choice",
                mode=mode,
                choices=", ".join(f"{c}{symbol}" for c in temp.choices),
            )
        return value
    step = _step(temp, resources)
    value = temp.minimum + round((temperature - temp.minimum) / step) * step
    if not temp.minimum <= value <= temp.maximum:
        raise CookStartError(
            "cook_temperature_out_of_range",
            mode=mode,
            min=f"{temp.minimum}{symbol}",
            max=f"{temp.maximum}{symbol}",
        )
    return value


# ---------------------------------------------------------------------------
# Choices held while idle
# ---------------------------------------------------------------------------

PARAM_MODE = "mode"
PARAM_TEMPERATURE = "temperature"
PARAM_DURATION = "duration"
# The start button's own marker.
PARAM_START = "start"


def _device_values(resources: dict) -> tuple:
    return (
        current_mode(resources),
        _temp_item(resources).get(_DESIRED),
        (resources.get(OPERATION_HREF) or {}).get(_OPERATION_TIME),
    )


class HeldCook:
    """The mode, setpoint and cook time chosen while the oven is idle,
    kept here rather than written (the board discards them from `Ready`)
    until a start sends them.

    Same rules as the 8888 washer's held course (#519): a new mode brings
    its own defaults, so it drops a held setpoint and time; and everything
    is dropped once the oven leaves `Ready` or its own mode, setpoint or
    time changes, since the panel has been used.
    """

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._base: tuple | None = None

    def clear(self) -> None:
        self.values = {}
        self._base = None

    def hold(self, param: str, value: Any, resources: dict) -> None:
        """Validate `value` against the declaration and hold it. Raises
        CookStartError when the board rules it out."""
        self._expire(resources)
        # Checked as the start would be: against the held mode (else the
        # board's default) and whatever else is already held.
        if param == PARAM_MODE:
            plan_start(resources, mode=value, complete=False)
            held = {PARAM_MODE: value}
        else:
            held = {**self.values, param: value}
            plan = plan_start(
                resources,
                mode=held.get(PARAM_MODE),
                temperature=held.get(PARAM_TEMPERATURE),
                duration=held.get(PARAM_DURATION),
                complete=False,
            )
            held = {
                **held,
                param: plan.temperature if param == PARAM_TEMPERATURE else plan.duration,
            }
        if not self.values:
            self._base = _device_values(resources)
        self.values = held

    def _expire(self, resources: dict) -> None:
        if self.values and (not is_idle(resources) or _device_values(resources) != self._base):
            self.clear()

    def overlay(self, resources: dict) -> dict:
        """`resources` as the start would leave them: held values, then the
        held mode's own defaults for whatever is not held. Returns
        `resources` itself when nothing is held."""
        self._expire(resources)
        if not self.values:
            return resources
        view = dict(resources)
        # Without a held mode the start uses the board's default mode, so
        # that is what reads back.
        default = (resources.get(MODE_HREF) or {}).get("x.com.samsung.da.defaultMode")
        mode = self.values.get(PARAM_MODE) or (
            default if default in startable_modes(resources) else None
        )
        if mode and MODE_HREF in view:
            view[MODE_HREF] = {**view[MODE_HREF], _MODES: [mode]}
        spec = mode_specs(resources).get(mode or "")
        unit = _UNIT_KEYS.get(device_unit(resources) or "")
        temp = spec.temps.get(unit) if spec and unit else None
        target = self.values.get(PARAM_TEMPERATURE, temp.default if temp else None)
        items = (view.get(TEMPERATURES_HREF) or {}).get(_ITEMS)
        if target is not None and items:
            items = [dict(i) for i in items]
            items[0][_DESIRED] = str(target)
            view[TEMPERATURES_HREF] = {**view[TEMPERATURES_HREF], _ITEMS: items}
        seconds = self.values.get(PARAM_DURATION, spec.time_default if spec else None)
        if seconds is not None and OPERATION_HREF in view:
            view[OPERATION_HREF] = {**view[OPERATION_HREF], _OPERATION_TIME: format_hms(seconds)}
        return view

    def plan(self, resources: dict) -> CookPlan:
        """The start the held values describe."""
        self._expire(resources)
        return plan_start(
            resources,
            mode=self.values.get(PARAM_MODE),
            temperature=self.values.get(PARAM_TEMPERATURE),
            duration=self.values.get(PARAM_DURATION),
        )

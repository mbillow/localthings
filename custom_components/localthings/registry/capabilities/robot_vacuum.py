"""Robot vacuum (POWERbot R7040, issue #659).

The robot reports its whole state as `<Group>_<Value>` tokens in one
`/mode/vs/0` modes list: `Control_*` is what the robot is doing,
`Cleaning_*` the cleaning program, `Turbo_*` suction. `supportedModes`
lists every token it accepts. These match SmartThings' robotCleanerMovement,
robotCleanerCleaningMode and robotCleanerTurboMode capabilities value for
value, which is what the activity mapping below rests on.

Writes send one token, e.g. `{"x.com.samsung.da.modes": ["Control_Homing"]}`,
the shape this family's other `/mode/vs/0` writes use. None is confirmed on
this robot yet (issue #659 asks the reporter to try them); a token the robot
rejects comes back as a 4.xx, not an action.
"""

from __future__ import annotations

from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc, VacuumDesc
from .common import int_or_none

HREF_MODE = "/mode/vs/0"
MODES_FIELD = "x.com.samsung.da.modes"
SUPPORTED_MODES_FIELD = "x.com.samsung.da.supportedModes"

CONTROL = "Control"
CLEANING = "Cleaning"
TURBO = "Turbo"
SOUND = "Sound"
ACCESS = "Access"
REPEATED_CLEANING = "RepeatedCleaning"

# Control_* -> Home Assistant vacuum activity. Values without a clear
# activity (Reserve, ViewSet, After, PowerOff) are left to the Cleaning_*
# fallback in activity().
_CONTROL_ACTIVITY = {
    "Cleaning": "cleaning",
    "Point": "cleaning",
    "Homing": "returning",
    "Charging": "docked",
    "Idle": "idle",
    "Pause": "paused",
    "Alarm": "error",
}

# Cleaning_* programs that start a run. Stop is the "not cleaning" value.
START_PROGRAM = "Auto"
SPOT_PROGRAM = "Part"
STOP_PROGRAM = "Stop"

# Turbo_* -> HA fan speed. HA's own vocabulary where one fits; SmartThings'
# turbo mode adds Silence/ExtraSilence on other models, so those are mapped
# too rather than shown raw.
_TURBO_TO_FAN_SPEED = {
    "Off": "normal",
    "On": "turbo",
    "Silence": "quiet",
    "ExtraSilence": "extra_quiet",
}
_FAN_SPEED_TO_TURBO = {v: k for k, v in _TURBO_TO_FAN_SPEED.items()}


def _tokens(rep: dict, field: str) -> list[str]:
    value = (rep or {}).get(field)
    if not isinstance(value, list):
        return []
    return [t for t in value if isinstance(t, str)]


def group_value(rep: dict, group: str) -> str | None:
    """The value of the `<group>_` token in the current modes list."""
    prefix = group + "_"
    for token in _tokens(rep, MODES_FIELD):
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def supported_values(rep: dict, group: str) -> list[str]:
    """Every `<group>_` value the robot advertises, in its own order."""
    prefix = group + "_"
    return [t[len(prefix) :] for t in _tokens(rep, SUPPORTED_MODES_FIELD) if t.startswith(prefix)]


def supports(rep: dict, group: str, value: str) -> bool:
    return f"{group}_{value}" in _tokens(rep, SUPPORTED_MODES_FIELD)


def activity(rep: dict) -> str | None:
    """HA vacuum activity, from Control_* first, then the cleaning program."""
    control = group_value(rep, CONTROL)
    if control in _CONTROL_ACTIVITY:
        return _CONTROL_ACTIVITY[control]
    program = group_value(rep, CLEANING)
    if program is not None and program != STOP_PROGRAM:
        return "cleaning"
    return None


def fan_speed(value: str | None) -> str | None:
    if value is None:
        return None
    return _TURBO_TO_FAN_SPEED.get(value, value.lower())


def fan_speed_list(rep: dict) -> list[str]:
    return [s for v in supported_values(rep, TURBO) if (s := fan_speed(v)) is not None]


def turbo_value(speed: str, rep: dict) -> str | None:
    """The Turbo_* value for an HA fan speed, if the robot supports it."""
    candidates = [_FAN_SPEED_TO_TURBO.get(speed, speed)]
    candidates += [v for v in supported_values(rep, TURBO) if v.lower() == speed]
    return next((v for v in candidates if supports(rep, TURBO, v)), None)


def _mode_write(token: str):
    return ["mode", "vs", "0"], {MODES_FIELD: [token]}


def _vacuum_write(payload, rep, href=None):
    """`(group, value)` from vacuum.py -> one modes token."""
    group, value = payload
    if not supports(rep, group, value):
        return None
    return _mode_write(f"{group}_{value}")


def _group_write(group: str):
    def write(payload, rep, href=None):
        if not supports(rep, group, payload):
            return None
        return _mode_write(f"{group}_{payload}")

    return write


def _group_options(group: str):
    return lambda resources: supported_values(resources.get(HREF_MODE) or {}, group)


def _has_group(group: str):
    return lambda rep, resources: bool(supported_values(rep, group))


def _lower(value: str | None) -> str | None:
    return value.lower() if value is not None else None


VACUUM = Capability(
    href=HREF_MODE,
    poll_tier="hot",
    entities=(
        VacuumDesc(
            key="vacuum",
            translation_key="robot_vacuum",
            rep_fn=activity,
            write_fn=_vacuum_write,
            merges_modes=True,
        ),
        # What the robot reports it is doing, unreduced: Homing, Point,
        # Reserve and the rest all collapse into a handful of HA activities.
        SensorDesc(
            key="robot_status",
            device_class="enum",
            icon="mdi:robot-vacuum",
            entity_category="diagnostic",
            rep_fn=lambda rep: _lower(group_value(rep, CONTROL)),
            options=lambda resources: [
                v.lower() for v in supported_values(resources.get(HREF_MODE) or {}, CONTROL)
            ],
            exists_fn=_has_group(CONTROL),
        ),
        BinarySensorDesc(
            key="battery_charging",
            device_class="battery_charging",
            rep_fn=lambda rep: (
                None if (c := group_value(rep, CONTROL)) is None else c == "Charging"
            ),
            exists_fn=_has_group(CONTROL),
        ),
        # Picking a program starts it, as in the SmartThings app; Stop ends
        # the run.
        SelectDesc(
            key="cleaning_mode",
            icon="mdi:broom",
            rep_fn=lambda rep: group_value(rep, CLEANING),
            options=_group_options(CLEANING),
            exists_fn=_has_group(CLEANING),
            write_fn=_group_write(CLEANING),
            merges_modes=True,
        ),
        SelectDesc(
            key="robot_sound",
            icon="mdi:volume-high",
            entity_category="config",
            rep_fn=lambda rep: group_value(rep, SOUND),
            options=_group_options(SOUND),
            exists_fn=_has_group(SOUND),
            write_fn=_group_write(SOUND),
            merges_modes=True,
        ),
        # switch.py sends "On"/"Off", the group's own values.
        SwitchDesc(
            key="repeated_cleaning",
            icon="mdi:repeat",
            entity_category="config",
            rep_fn=lambda rep: (
                None if (v := group_value(rep, REPEATED_CLEANING)) is None else v == "On"
            ),
            exists_fn=_has_group(REPEATED_CLEANING),
            write_fn=_group_write(REPEATED_CLEANING),
            merges_modes=True,
        ),
        # Read-only like every other child lock here (common.KIDS_LOCK_GENERIC):
        # On means unlocked, per HA's lock device class. That Access_ is the
        # child lock is inferred from the Lock/UnLock pair alone.
        BinarySensorDesc(
            key="child_lock",
            device_class="lock",
            entity_category="diagnostic",
            rep_fn=lambda rep: None if (a := group_value(rep, ACCESS)) is None else a != "Lock",
            exists_fn=_has_group(ACCESS),
        ),
    ),
)

# The OCF-standard battery resource; the vendor copy below only stands in
# when it's missing.
BATTERY = Capability(
    href="/energy/battery/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="battery",
            field="charge",
            device_class="battery",
            state_class="measurement",
            unit="%",
            value_fn=int_or_none,
        ),
    ),
)

BATTERY_VS_FALLBACK = Capability(
    href="/energy/battery/vs/0",
    match_fn=lambda rep, resources: "/energy/battery/0" not in resources,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="battery",
            field="x.com.samsung.da.batteryCharge",
            device_class="battery",
            state_class="measurement",
            unit="%",
            value_fn=int_or_none,
        ),
    ),
)

# Only 'Stop' has been seen (docked). Probably the manual-drive direction;
# shown raw until a dump taken mid-run shows the other values.
MOVEMENT = Capability(
    href="/movement/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="robot_movement",
            field="x.com.samsung.da.behavior",
            icon="mdi:arrow-all",
            entity_category="diagnostic",
            enabled_default=False,
        ),
    ),
)

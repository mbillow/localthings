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
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    VacuumDesc,
)
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

# Control_* -> Home Assistant vacuum activity. The rest (Reserve, ViewSet,
# After, PowerOff) read as idle: none of them is the robot moving.
_CONTROL_ACTIVITY = {
    "Cleaning": "cleaning",
    "Point": "cleaning",
    "PatternMap": "cleaning",
    "Homing": "returning",
    "Charging": "docked",
    "Charged": "docked",
    "FlexCharged": "docked",
    "Idle": "idle",
    "Pause": "paused",
    "Alarm": "error",
}

# The Jet Bot Combo (JETBOT_COMBOT_9X00_24K, #838) lists dozens more
# Control_* values, read by their shape: every *Homing is a way back to the
# station, every *Pause a pause, and the station's own work (mop washing and
# drying, water, dust emptying, descaling) happens with the robot docked.
_DOCKED_PREFIXES = (
    "MopWash",
    "MopSterilization",
    "MopDry",
    "SupplyingWater",
    "PreparingWater",
    "Dehydration",
    "DrainingWater",
    "Descaling",
    "EmitDust",
)


def _control_activity(control: str) -> str:
    if control in _CONTROL_ACTIVITY:
        return _CONTROL_ACTIVITY[control]
    if control.endswith("Homing"):
        return "returning"
    if control.endswith("Pause"):
        return "paused"
    if control.endswith("Charging") or control.startswith(_DOCKED_PREFIXES):
        return "docked"
    return "idle"


# Cleaning_* programs that start a run. Stop is the "not cleaning" value.
# Spot is Part on the R7040 and Spot on the Jet Bot Combo.
START_PROGRAM = "Auto"
SPOT_PROGRAMS = ("Part", "Spot")
STOP_PROGRAM = "Stop"
CLEANING_METHOD = "CleaningMethod"
WATER_LEVEL = "WaterLevel"
DUST_EMISSION = "DustEmission"
MOP_HOLDER = "MopHolder"
# The app's "Cleaning option" (/view/vs/0 lists CleaningOption_Visible).
CLEANING_OPTION = "Option"

# Turbo_* -> HA fan speed. HA's own vocabulary where one fits; SmartThings'
# turbo mode adds Silence/ExtraSilence on other models, so those are mapped
# too rather than shown raw.
_TURBO_TO_FAN_SPEED = {
    "Off": "normal",
    "On": "turbo",
    "Silence": "quiet",
    "ExtraSilence": "extra_quiet",
    "UltraSilence": "ultra_quiet",
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
    """HA vacuum activity from Control_*, or from the cleaning program on a
    rep that carries no Control_* token."""
    control = group_value(rep, CONTROL)
    if control is not None:
        return _control_activity(control)
    program = group_value(rep, CLEANING)
    if program is not None and program != STOP_PROGRAM:
        return "cleaning"
    return None


def spot_program(rep: dict) -> str | None:
    """This robot's spot-cleaning program, if it has one."""
    return next((p for p in SPOT_PROGRAMS if supports(rep, CLEANING, p)), None)


def is_charging(control: str | None) -> bool | None:
    return None if control is None else control.endswith("Charging")


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


def _group_write(group: str):
    def write(payload, rep, href=None):
        if not supports(rep, group, payload):
            return None
        return _mode_write(f"{group}_{payload}")

    return write


def _vacuum_write(payload, rep, href=None):
    """`(group, value)` from vacuum.py -> one modes token."""
    group, value = payload
    return _group_write(group)(value, rep)


def _expect_control(body: dict) -> dict:
    """Optimistic cache body for a Cleaning_* write: also assume the
    Control_* state it leads to, since activity() reads Control_* and the
    settle guard holds back the poll that would report it."""
    tokens = list(body.get(MODES_FIELD) or [])
    program = next((t[len(CLEANING) + 1 :] for t in tokens if t.startswith(CLEANING + "_")), None)
    if program is None:
        return body
    control = "Idle" if program == STOP_PROGRAM else "Cleaning"
    return {**body, MODES_FIELD: [*tokens, f"{CONTROL}_{control}"]}


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
            optimistic_fn=_expect_control,
        ),
        # What the robot reports it is doing, unreduced: Homing, Point,
        # Reserve and the rest all collapse into a handful of HA activities.
        SensorDesc(
            key="robot_status",
            device_class="enum",
            icon="mdi:robot-vacuum",
            entity_category="diagnostic",
            rep_fn=lambda rep: _lower(group_value(rep, CONTROL)),
            options=lambda resources: [v.lower() for v in _group_options(CONTROL)(resources)],
            exists_fn=_has_group(CONTROL),
        ),
        BinarySensorDesc(
            key="battery_charging",
            device_class="battery_charging",
            rep_fn=lambda rep: is_charging(group_value(rep, CONTROL)),
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
            optimistic_fn=_expect_control,
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
        # The Jet Bot Combo's mop and dock settings (#838), same token shape.
        SelectDesc(
            key="cleaning_method",
            icon="mdi:robot-vacuum-variant",
            entity_category="config",
            rep_fn=lambda rep: group_value(rep, CLEANING_METHOD),
            options=_group_options(CLEANING_METHOD),
            exists_fn=_has_group(CLEANING_METHOD),
            write_fn=_group_write(CLEANING_METHOD),
            merges_modes=True,
        ),
        SelectDesc(
            key="water_level",
            icon="mdi:water",
            entity_category="config",
            rep_fn=lambda rep: group_value(rep, WATER_LEVEL),
            options=_group_options(WATER_LEVEL),
            exists_fn=_has_group(WATER_LEVEL),
            write_fn=_group_write(WATER_LEVEL),
            merges_modes=True,
        ),
        SelectDesc(
            key="cleaning_option",
            icon="mdi:tune-variant",
            entity_category="config",
            rep_fn=lambda rep: group_value(rep, CLEANING_OPTION),
            options=_group_options(CLEANING_OPTION),
            exists_fn=_has_group(CLEANING_OPTION),
            write_fn=_group_write(CLEANING_OPTION),
            merges_modes=True,
        ),
        # DustEmission_On/Off is the station's auto empty; _Start empties now.
        SwitchDesc(
            key="auto_empty",
            icon="mdi:delete-restore",
            entity_category="config",
            rep_fn=lambda rep: (
                None if (v := group_value(rep, DUST_EMISSION)) is None else v != "Off"
            ),
            exists_fn=lambda rep, resources: supports(rep, DUST_EMISSION, "Off"),
            write_fn=_group_write(DUST_EMISSION),
            merges_modes=True,
        ),
        ButtonDesc(
            key="empty_dust_bin",
            field="",
            payload="Start",
            icon="mdi:delete-empty",
            exists_fn=lambda rep, resources: supports(rep, DUST_EMISSION, "Start"),
            write_fn=_group_write(DUST_EMISSION),
            write_only=True,
        ),
        BinarySensorDesc(
            key="mop_attached",
            icon="mdi:water-sync",
            entity_category="diagnostic",
            rep_fn=lambda rep: (
                None if (v := group_value(rep, MOP_HOLDER)) is None else v == "Attach"
            ),
            exists_fn=_has_group(MOP_HOLDER),
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
        # batteryCharge on the R7040, battery on the Jet Bot Combo (#838).
        SensorDesc(
            key="battery",
            device_class="battery",
            state_class="measurement",
            unit="%",
            rep_fn=lambda rep: int_or_none(
                rep.get("x.com.samsung.da.batteryCharge", rep.get("x.com.samsung.da.battery"))
            ),
        ),
    ),
)

# --- Jet Bot Combo resources (JETBOT_COMBOT_9X00_24K, #838) ----------------
# Writes below follow the field shape each resource reports and need live
# confirmation.


def _status_problem(field: str):
    """A dock/filter status: anything but 'normal' is a problem."""
    return lambda rep: None if (v := rep.get(field)) is None else v != "normal"


def _true_string(value) -> bool | None:
    return None if value is None else str(value).lower() == "true"


BATTERY_HEALTH = Capability(
    href="/battery/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="battery_health",
            field="x.com.samsung.da.soh",
            icon="mdi:battery-heart-variant",
            unit="%",
            state_class="measurement",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
    ),
)

DUST_BAG = Capability(
    href="/component/station/dustbag/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="dust_bag_full",
            device_class="problem",
            icon="mdi:delete-alert",
            rep_fn=lambda rep: (
                None if (v := rep.get("x.com.samsung.da.status")) is None else v == "full"
            ),
        ),
    ),
)

DOCK_FILTER = Capability(
    href="/component/station/filter/hepafilter/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="dock_filter",
            device_class="problem",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            rep_fn=_status_problem("x.com.samsung.da.filterStatus"),
        ),
    ),
)

MOTOR_FILTER = Capability(
    href="/filter/motorfilter/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="motor_filter",
            device_class="problem",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            rep_fn=_status_problem("x.com.samsung.da.filterStatus"),
        ),
    ),
)

LIGHTING = Capability(
    href="/lighting/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="robot_lighting",
            field="x.com.samsung.da.lighting",
            icon="mdi:lightbulb-on",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["lighting", "vs", "0"],
                {"x.com.samsung.da.lighting": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# The Jet Bot Combo's child lock has its own href and no Access_ token, so
# the two never bind together. On means unlocked, as for Access_ above.
CHILD_LOCK = Capability(
    href="/child/lock/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="child_lock",
            field="x.com.samsung.da.enable",
            device_class="lock",
            entity_category="diagnostic",
            value_fn=lambda v: None if (locked := _true_string(v)) is None else not locked,
        ),
    ),
)

FIND_ROBOT = Capability(
    href="/findrobotcleaner/vs/0",
    poll_tier="cold",
    entities=(
        ButtonDesc(
            key="find_robot",
            field="",
            payload="Start",
            icon="mdi:map-marker-question",
            exists_fn=lambda rep, resources: (
                "Start" in (rep.get("x.com.samsung.da.supportedFindAlarm") or ())
            ),
            write_fn=lambda p, rep, href=None: (
                ["findrobotcleaner", "vs", "0"],
                {"x.com.samsung.da.findAlarm": p},
            ),
        ),
    ),
)


def _station_status(field: str):
    def read(rep):
        status = (rep.get("x.com.samsung.da.mopStationStatus") or {}).get(field)
        return None if status is None else status != "Good"

    return read


def _has_station_status(field: str):
    return lambda rep, resources: field in (rep.get("x.com.samsung.da.mopStationStatus") or {})


# The mop station's tanks: "Good" is the only value seen, so anything else
# reads as a problem.
STATION = Capability(
    href="/station/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="clean_water_tank",
            device_class="problem",
            icon="mdi:cup-water",
            rep_fn=_station_status("x.com.samsung.da.cleanWater"),
            exists_fn=_has_station_status("x.com.samsung.da.cleanWater"),
        ),
        BinarySensorDesc(
            key="dirty_water_tank",
            device_class="problem",
            icon="mdi:cup-water",
            rep_fn=_station_status("x.com.samsung.da.dirtyWater"),
            exists_fn=_has_station_status("x.com.samsung.da.dirtyWater"),
        ),
        BinarySensorDesc(
            key="station_water_leak",
            device_class="moisture",
            rep_fn=lambda rep: _true_string(
                (rep.get("x.com.samsung.da.mopStationStatus") or {}).get(
                    "x.com.samsung.da.waterLeak"
                )
            ),
            exists_fn=_has_station_status("x.com.samsung.da.waterLeak"),
        ),
    ),
)

DO_NOT_DISTURB = Capability(
    href="/dnd/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="do_not_disturb",
            field="x.com.samsung.da.value",
            icon="mdi:sleep",
            entity_category="diagnostic",
            value_fn=_true_string,
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


# Bound with no entity: nothing here is state worth automating on, or it
# can't be read without a unit or vocabulary no dump has confirmed yet.
COVERAGE: list[Capability] = [
    Capability(href=href)
    for href in (
        # Map editing, rooms, zones and object recognition: per-map data the
        # app draws, keyed by mapId.
        *(
            f"/map/{name}/vs/0"
            for name in (
                "areaclean", "areaname", "areaoption", "areaorder", "carpet", "cleaninfo",
                "climbzone", "divide", "floorpattern", "list", "merge", "objectclean",
                "objectinfo", "obstacle", "obstacleinfo", "padlocation", "recognitioninfo",
                "restrict", "spotclean", "spotinfo", "stainclean",
            )
        ),
        "/map/vs/0",
        # Camera, home monitoring, pet care and child care: video and
        # app-scheduled features.
        "/child/care/recording/vs/0",
        "/child/care/schedule/vs/0",
        "/child/care/vs/0",
        "/monitoring/auto/vs/0",
        "/monitoring/block/vs/0",
        "/monitoring/push/vs/0",
        "/monitoring/spot/vs/0",
        "/monitoring/status/vs/0",
        "/pet/cleaning/vs/0",
        "/pet/find/vs/0",
        "/pet/installedappid/vs/0",
        "/pet/monitoring/vs/0",
        "/pet/status/vs/0",
        "/sound/detection/vs/0",
        "/streaming/vs/0",
        "/video/stream/vs/0",
        # Media playback, voice and the Bixby speaker's own volume.
        "/audio/vs/0",
        "/clip/audio/vs/0",
        "/media/playback/repeat/vs/0",
        "/media/playback/vs/0",
        "/mic/vs/0",
        "/music/playlist/vs/0",
        "/sec/networkaudio/audio",
        "/settings/sound/language/vs/0",
        "/settings/sound/output/vs/0",
        "/settings/sound/tone/vs/0",
        "/settings/sound/voice/vs/0",
        # Same choice as the Sound_ token the robot_sound select drives.
        "/settings/sound/mode/vs/0",
        # Schedules, previews, notifications and the app's own screen layout.
        "/notification/context/robotVacuum/operation/vs/0",
        "/notification/context/vs/0",
        "/preview/vs/0",
        "/schedule/vs/0",
        "/schedulewithoffset/vs/0",
        "/view/vs/0",
        # Command-only or one-off procedures: manual drive, homing, demo,
        # calibration, a manual dust emptying, descaling.
        "/demo/vs/0",
        "/descaling/vs/0",
        "/direction/vs/0",
        "/homing/vs/0",
        "/manual/dustemission/vs/0",
        "/self/calibration/vs/0",
        # Remaining-time counters with no unit to read them in, mop pattern
        # and recleaning settings, capability flags and onboarding state.
        "/detailedcleaning/vs/0",
        "/laboratory/vs/0",
        "/maintenance/vs/0",
        "/modelinfo/vs/0",
        "/mop/settings/vs/0",
        "/ready/join/vs/0",
        "/settings/vs/0",
        "/station/time/vs/0",
        "/wifi/level/vs/0",
    )
]  # fmt: skip

"""Capabilities for the Samsung microwave family (TP1X_DA-KS-MICROWAVE-*
class boards, both combi units and plain microwaves).

Shares the oven board family's cavity/cook-cycle resource shape
(`/operational/state/vs/0`, `/doors/vs/0`, `/connected/vs/0`,
`/recipe/cook/vs/0`) -- those Capability objects are reused directly from
oven.py in by_type/microwave.py rather than duplicated. What's genuinely
different from an oven, and defined fresh here:

  * Cooking-mode vocabulary: MicroWave/MicroWaveGrill/MicroWaveConvection/
    KeepWarm never appear on an oven's /mode/vs/0, and some shared-sounding
    modes are spelled differently (e.g. 'AirFryer', not oven.py's
    'AirFry') -- a distinct SelectDesc and mode list, not oven.OVEN_MODE.
  * Setpoint bounds: this family's Convection/MicroWaveConvection modeSpec
    (issue #121) reports 40-200°C / step 5, not oven.py's 30-270°C range.
  * Cavity: /oven/vs/0 here also carries a `powerLevel` field (100W-900W)
    that plain ovens don't report -- exposed as its own sensor.
  * Lamp: this family's option-array token is bare 'Lamp' (issue #137), not
    oven.py's 'UpperLamp', and genuinely absent on the combi dump (issue
    #121), so it's exists_fn-gated rather than assumed universal. The three
    confirmed tokens are Off/Low/High (issues #152/#181), exposed as a native
    HA brightness light at 0/50/100 percent.
  * Filter reminder / end signal reminder: bare 'FilterRemind'/'RemindBeep'
    option-array tokens (issue #181), gated with exists_fn like Lamp since
    the MW7300B combi dump has neither.

Cooking-mode writes are unproven here, same caveat as oven.py's OVEN_MODE
-- exposed as a SelectDesc for fidelity, first real-world write is the test.

DAWIT 3.0 generation (issue #433, OT80H30-class over-the-range combi):
this board answers none of the hrefs above -- no /oven/vs/0, /mode/vs/0,
/temperatures/vs/0, /doors/vs/0, /operational/state/vs/0. The whole cavity
(mode, door, child lock, microwave power level, cook time) is one
bare-field `/oven/status/vs/0`, with per-mode bounds in `/oven/spec/vs/0`
and panel preferences in `/oven/settings/status/vs/0`.

Every resource in this generation is read-only over the local API: issue
#433's reporter got CoAP 4.05 (Method Not Allowed) writing all five of
them, and the board declares them `oic.if.s` (sensor) where all 77
writable hrefs in the fixture corpus declare `oic.if.a`. Cloud control
still works from the SmartThings app, so what's missing is a local write
path, not permission. Same call as common.py's KIDS_LOCK_VS_FALLBACK
(issues #181/#183): no write_fn anywhere below, rather than controls that
always error.
"""

from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    LightDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from .common import int_or_none, normalize_temp_unit, parse_iso_utc
from .laundry import option_value, option_write

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Union of every mode seen across the two known dumps (issues #121, #137).
# No dump has shown every mode below on one device -- the select surfaces
# whatever a given board's own supportedModes reports; an entry here a
# device never sends just never gets picked.
_MICROWAVE_MODES = (
    "NoOperation",
    "MicroWave",
    "MicroWaveGrill",
    "MicroWaveConvection",
    "Convection",
    "AirFryer",
    "Grill",
    "Autocook",
    "AutocookCustom",
    "Deodorization",
    "KeepWarm",
)

# Convection/MicroWaveConvection modeSpec on issue #121's dump: 40-200°C,
# step 5. No Fahrenheit dump exists for this family, unlike oven.py's own
# independently-verified F bounds, so this module only exposes the
# setpoint control when the live unit is Celsius (see _microwave_temp_unit).
SETPOINT_MIN_C = 40
SETPOINT_MAX_C = 200
SETPOINT_STEP_C = 5


def _microwave_temp_unit(rep):
    """Same shape as oven.py's _oven_temp_unit. Both known dumps report
    'Celsius'; kept live rather than hardcoded (issue #7)."""
    items = rep.get("x.com.samsung.da.items") or []
    unit = items[0].get("x.com.samsung.da.unit") if items else None
    return normalize_temp_unit(unit, default="°C")


def _setpoint_write(p, rep, href=None):
    """RMW write to /temperatures/vs/0 items array -- unproven for this
    family, same "exposed for fidelity" caveat as the mode select."""
    try:
        temp = float(p)
    except (TypeError, ValueError):
        return None
    temp_i = int(round(temp / SETPOINT_STEP_C) * SETPOINT_STEP_C)
    if not (SETPOINT_MIN_C <= temp_i <= SETPOINT_MAX_C):
        return None
    items = rep.get("x.com.samsung.da.items")
    if not items:
        return None
    items = [dict(it) for it in items]
    items[0]["x.com.samsung.da.desired"] = str(temp_i)
    return ["temperatures", "vs", "0"], {"x.com.samsung.da.items": items}


def _power_level_watts(v):
    """'100W'..'900W' (issue #121) or a bare '0' (issue #137) -> int watts."""
    if v is None:
        return None
    s = str(v).strip()
    if s.upper().endswith("W"):
        s = s[:-1]
    return int_or_none(s)


def _cooking_mode_options(resources):
    """Live mode list from the device's own supportedModes when reported
    (both known dumps do); the union-of-all-dumps _MICROWAVE_MODES guess
    otherwise. Same live-first, static-fallback pattern as
    oven._oven_mode_options -- a fixed list would offer modes a unit
    doesn't have (issue #152 reports only 4 of _MICROWAVE_MODES' 11)."""
    rep = resources.get("/mode/vs/0") or {}
    live = rep.get("x.com.samsung.da.supportedModes")
    return list(live) if live else list(_MICROWAVE_MODES)


def _mode_write(p, rep, href=None):
    valid = rep.get("x.com.samsung.da.supportedModes") or _MICROWAVE_MODES
    if p not in valid:
        return None
    return ["mode", "vs", "0"], {"x.com.samsung.da.modes": [p]}


def _sound_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    if not rep.get("x.com.samsung.da.options"):
        return None
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Sound", p),
    }


def _lamp_exists(rep, resources):
    return option_value(rep.get("x.com.samsung.da.options"), "Lamp") is not None


def _lamp_brightness(options):
    return {
        "Off": 0,
        "Low": 128,
        "High": 255,
    }.get(option_value(options, "Lamp"))


def _filter_remind_exists(rep, resources):
    return option_value(rep.get("x.com.samsung.da.options"), "FilterRemind") is not None


def _remind_beep_exists(rep, resources):
    return option_value(rep.get("x.com.samsung.da.options"), "RemindBeep") is not None


def _lamp_write(p, rep, href=None):
    try:
        brightness = float(p)
    except (TypeError, ValueError):
        return None
    if not rep.get("x.com.samsung.da.options"):
        return None
    if brightness <= 0:
        token = "Off"
    elif brightness <= 128:
        token = "Low"
    else:
        token = "High"
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Lamp", token),
    }


def _filter_remind_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    if not rep.get("x.com.samsung.da.options"):
        return None
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("FilterRemind", p),
    }


def _remind_beep_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    if not rep.get("x.com.samsung.da.options"):
        return None
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("RemindBeep", p),
    }


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

MICROWAVE_CAVITY = Capability(
    href="/oven/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(key="cavity_state", field="x.com.samsung.da.state"),
        SensorDesc(
            key="power_level",
            field="x.com.samsung.da.powerLevel",
            unit="W",
            value_fn=_power_level_watts,
        ),
    ),
)

MICROWAVE_SETPOINT = Capability(
    href="/temperatures/vs/0",
    poll_tier="hot",
    entities=(
        NumberDesc(
            key="setpoint",
            field="x.com.samsung.da.items",
            device_class="temperature",
            unit_fn=_microwave_temp_unit,
            native_min=float(SETPOINT_MIN_C),
            native_max=float(SETPOINT_MAX_C),
            step=float(SETPOINT_STEP_C),
            icon="mdi:thermometer-chevron-up",
            exists_fn=lambda rep, resources: _microwave_temp_unit(rep) == "°C",
            value_fn=lambda items: int_or_none(
                items[0].get("x.com.samsung.da.desired") if items else None
            ),
            write_fn=_setpoint_write,
        ),
        SensorDesc(
            key="current_temp_c",
            field="x.com.samsung.da.items",
            device_class="temperature",
            state_class="measurement",
            unit_fn=_microwave_temp_unit,
            value_fn=lambda items: int_or_none(
                items[0].get("x.com.samsung.da.current") if items else None
            ),
        ),
    ),
)

MICROWAVE_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="warm",
    entities=(
        # SelectDesc first — test_microwave_mode_options_nonempty uses entities[0]
        SelectDesc(
            key="cooking_mode",
            field="x.com.samsung.da.modes",
            icon="mdi:tune",
            options=_cooking_mode_options,
            value_fn=lambda v: v[0] if v else None,
            write_fn=_mode_write,
        ),
        SwitchDesc(
            key="sound",
            field="x.com.samsung.da.options",
            icon="mdi:volume-high",
            entity_category="config",
            value_fn=lambda opts: option_value(opts, "Sound") == "On",
            write_fn=_sound_write,
        ),
        LightDesc(
            key="lamp",
            field="x.com.samsung.da.options",
            icon="mdi:track-light",
            exists_fn=_lamp_exists,
            value_fn=_lamp_brightness,
            write_fn=_lamp_write,
        ),
        # issue #181: Filter Reminder / End Signal Reminder toggles, only on
        # boards carrying the FilterRemind_*/RemindBeep_* tokens; gated off
        # elsewhere (the MW7300B combi dump has neither).
        SwitchDesc(
            key="filter_remind",
            field="x.com.samsung.da.options",
            icon="mdi:air-filter",
            entity_category="config",
            exists_fn=_filter_remind_exists,
            value_fn=lambda opts: option_value(opts, "FilterRemind") == "On",
            write_fn=_filter_remind_write,
        ),
        SwitchDesc(
            key="remind_beep",
            field="x.com.samsung.da.options",
            icon="mdi:bell-ring",
            entity_category="config",
            exists_fn=_remind_beep_exists,
            value_fn=lambda opts: option_value(opts, "RemindBeep") == "On",
            write_fn=_remind_beep_write,
        ),
    ),
)

# ---------------------------------------------------------------------------
# DAWIT 3.0 generation (issue #433) -- see module docstring.
# ---------------------------------------------------------------------------


_STATUS_STATE_TO_OCF = {
    "ready": "idle",
    "run": "active",
    "running": "active",
    "pause": "pause",
    "paused": "pause",
    "end": "idle",
    "stop": "idle",
}


def _status_to_ocf(v):
    if v is None:
        return None
    return _STATUS_STATE_TO_OCF.get(str(v).lower(), v)


# The same catalog states select.cooking_mode already ships for the older
# generation, so one board's "Keep warm" reads like another's. An unlisted
# mode falls through raw -- sensor.py's `options` admits whatever the
# device reports.
_MODE_TO_STATE = {
    "NoOperation": "no_operation",
    "MicroWave": "micro_wave",
    "Autocook": "autocook",
    "KeepWarm": "keep_warm",
}


def _mode_state(name):
    return _MODE_TO_STATE.get(name, name)


def _mode_options(resources):
    """The board's own availableModeList. It omits the idle sentinel
    (`NoOperation`, what mode.name reads at rest); sensor.py's `options`
    admits the live value, so nothing has to union it in here."""
    rep = resources.get("/oven/status/vs/0") or {}
    return [_mode_state(m) for m in rep.get("availableModeList") or ()]


def _power_level_unit(rep):
    """The device names its own unit ('percentage') next to the value."""
    unit = (rep.get("microwavePowerLevel") or {}).get("unit")
    return "%" if unit == "percentage" else None


# `time` is in seconds: /oven/spec/vs/0's modeSpec caps it at 6039, which
# is 99*60 + 99 -- the 99:99 ceiling these panels count down from.
MICROWAVE_STATUS = Capability(
    href="/oven/status/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="machine_state",
            field="operation",
            icon="mdi:stove",
            device_class="enum",
            options=("idle", "active", "pause"),
            translation_key="machine_state",
            value_fn=_status_to_ocf,
        ),
        BinarySensorDesc(
            key="cycle_active",
            field="operation",
            device_class="running",
            value_fn=lambda v: _status_to_ocf(v) == "active",
        ),
        BinarySensorDesc(
            key="door_open",
            field="door",
            device_class="door",
            value_fn=lambda door: (door or {}).get("state") == "open",
        ),
        # device_class='lock' reads inverted, 'On' = unlocked -- same
        # polarity as common.KIDS_LOCK_GENERIC.
        BinarySensorDesc(
            key="child_lock",
            field="childLock",
            device_class="lock",
            value_fn=lambda v: str(v).lower() != "on",
        ),
        SensorDesc(
            key="cooking_mode",
            field="mode",
            icon="mdi:tune",
            device_class="enum",
            options=_mode_options,
            value_fn=lambda mode: _mode_state((mode or {}).get("name")),
        ),
        SensorDesc(
            key="power_level",
            field="microwavePowerLevel",
            icon="mdi:radar",
            unit_fn=_power_level_unit,
            value_fn=lambda v: int_or_none((v or {}).get("setting")),
        ),
        SensorDesc(
            key="cook_time",
            field="time",
            unit="s",
            device_class="duration",
            icon="mdi:timer",
            value_fn=lambda v: int_or_none((v or {}).get("setting")),
        ),
        SensorDesc(
            key="cook_time_remaining",
            field="time",
            unit="s",
            device_class="duration",
            state_class="measurement",
            icon="mdi:timer-sand",
            value_fn=lambda v: int_or_none((v or {}).get("remaining")),
        ),
        # Blank ('') on every dump seen, so the format is a guess: this
        # firmware does use offset-less ISO 8601 elsewhere (/alarms/vs/0's
        # triggeredTime), and a wrong guess reads unavailable rather than
        # misparsing.
        SensorDesc(
            key="cook_finish_time",
            field="time",
            device_class="timestamp",
            icon="mdi:timer-outline",
            value_fn=lambda v: parse_iso_utc((v or {}).get("completion")),
        ),
        # 'ready' alongside operation='ready' on the only dump seen --
        # raw passthrough, meaning unconfirmed beyond that.
        SensorDesc(
            key="sub_operation",
            field="subOperation",
            entity_category="diagnostic",
        ),
    ),
)

# Per-mode bounds and the mode list itself, read live by _mode_options and
# by nothing else -- a bare coverage marker like oven.py's OVEN_SPEC.
MICROWAVE_SPEC = Capability(href="/oven/spec/vs/0")

# Panel preferences. Read-only for the same reason as the cavity above, so
# these are binary sensors rather than the switches the field names invite.
# weightUnit/timeFormat get no entity: weightUnit only means something next
# to a weight, and this board's availableModeList has no defrost-by-weight
# mode; timeFormat governs the panel's own clock, not any value read here.
MICROWAVE_SETTINGS = Capability(
    href="/oven/settings/status/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="beep",
            field="beepSound",
            entity_category="diagnostic",
            icon="mdi:volume-high",
            value_fn=lambda v: str(v).lower() == "on",
        ),
        BinarySensorDesc(
            key="remind_beep",
            field="remindBeep",
            entity_category="diagnostic",
            icon="mdi:bell-ring",
            value_fn=lambda v: str(v).lower() == "on",
        ),
        BinarySensorDesc(
            key="display_time_auto_sync",
            field="displayTimeAutoSync",
            entity_category="diagnostic",
            icon="mdi:clock-sync",
            value_fn=lambda v: str(v).lower() == "on",
        ),
    ),
)

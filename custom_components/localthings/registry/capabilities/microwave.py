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
    #121), so it's exists_fn-gated rather than assumed universal. 'On' has
    never been observed as a value; the only confirmed non-Off token is
    'High' (issue #152) -- the switch treats any non-Off/non-None value as
    "on" for reads and writes back 'High'/'Off'.
  * Filter reminder / end signal reminder: bare 'FilterRemind'/'RemindBeep'
    option-array tokens (issue #181), gated with exists_fn like Lamp since
    the MW7300B combi dump has neither.

Cooking-mode writes are unproven here, same caveat as oven.py's OVEN_MODE
-- exposed as a SelectDesc for fidelity, first real-world write is the test.

DAWIT 3.0 generation (issue #433, OT80H30-class over-the-range combi):
this board answers none of the hrefs above -- no /oven/vs/0, /mode/vs/0,
/temperatures/vs/0, /doors/vs/0, /operational/state/vs/0. Instead the
whole cavity (mode, door, child lock, microwave power level, cook time) is
one bare-field `/oven/status/vs/0` resource (no `x.com.samsung.da.`
prefix, same plain-camelCase convention range.py's cooktop capabilities
already document), with static per-mode bounds split into a sibling
`/oven/spec/vs/0` and separate user-preference toggles in
`/oven/settings/status/vs/0`. `MICROWAVE_STATUS`/`MICROWAVE_SETTINGS`
below are this generation's fresh capabilities; `MICROWAVE_SPEC` is a bare
coverage marker like oven.py's OVEN_SPEC -- its cavityInfo.modeSpecList
(mode names, time bounds, microwavePowerLevel's powerLevelList) has no
per-entity descriptor to read it live from (NumberDesc bounds only ever
see their own href's rep, never a sibling's), so SETPOINT_MIN/MAX below
are copied from this one dump's modeSpecList rather than read dynamically
-- same "single-dump static bounds" caveat as SETPOINT_MIN_C above.
Nothing here is confirmed by a live write; see each write_fn's comment.
"""

from ..capability import Capability
from ..entities import BinarySensorDesc, NumberDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import int_or_none, normalize_temp_unit
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


def _filter_remind_exists(rep, resources):
    return option_value(rep.get("x.com.samsung.da.options"), "FilterRemind") is not None


def _remind_beep_exists(rep, resources):
    return option_value(rep.get("x.com.samsung.da.options"), "RemindBeep") is not None


def _lamp_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    if not rep.get("x.com.samsung.da.options"):
        return None
    # 'High'/'Off' are the two confirmed tokens (see module docstring);
    # 'On' has never been observed and likely isn't recognized.
    token = "High" if p == "On" else "Off"
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
        SwitchDesc(
            key="lamp",
            field="x.com.samsung.da.options",
            icon="mdi:track-light",
            exists_fn=_lamp_exists,
            value_fn=lambda opts: option_value(opts, "Lamp") not in (None, "Off"),
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

# Confirmed on issue #433's dump: MicroWave and KeepWarm's modeSpec both
# report {min: 1, max: 6039, interval: 1} -- seconds (a 6039-second/100-ish
# minute ceiling is plausible for a combi cook timer; a minutes reading
# would imply a multi-day cook, which isn't). Single dump, no cross-check
# -- see module docstring.
COOK_TIME_MIN_S = 1
COOK_TIME_MAX_S = 6039

# powerLevelList on the same dump's MicroWave modeSpec: 0-100 step 10.
POWER_LEVEL_MIN = 0
POWER_LEVEL_MAX = 100
POWER_LEVEL_STEP = 10

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


def _status_child_lock_write(p, rep, href=None):
    """Direct single-field PUT, no RMW needed -- same shape as range.py's
    cooktop_child_lock (issue #349: no device_class='lock' on a SwitchDesc,
    HA's switch platform only knows 'outlet'/'switch')."""
    if p not in ("On", "Off"):
        return None
    return ["oven", "status", "vs", "0"], {"childLock": p.lower()}


def _status_mode_write(p, rep, href=None):
    """RMW against the live availableModeList -- unconfirmed, same caveat
    as oven.py's OVEN_MODE (issue #433's reporter is asked to try this)."""
    valid = rep.get("availableModeList") or ()
    if p not in valid:
        return None
    mode = dict(rep.get("mode") or {})
    mode["name"] = p
    return ["oven", "status", "vs", "0"], {"mode": mode}


def _power_level_write(p, rep, href=None):
    try:
        level = int(round(float(p) / POWER_LEVEL_STEP) * POWER_LEVEL_STEP)
    except (TypeError, ValueError):
        return None
    if not (POWER_LEVEL_MIN <= level <= POWER_LEVEL_MAX):
        return None
    setting = dict(rep.get("microwavePowerLevel") or {})
    setting["setting"] = level
    return ["oven", "status", "vs", "0"], {"microwavePowerLevel": setting}


def _cook_time_write(p, rep, href=None):
    try:
        seconds = round(float(p))
    except (TypeError, ValueError):
        return None
    if not (COOK_TIME_MIN_S <= seconds <= COOK_TIME_MAX_S):
        return None
    setting = dict(rep.get("time") or {})
    setting["setting"] = seconds
    return ["oven", "status", "vs", "0"], {"time": setting}


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
        SwitchDesc(
            key="child_lock",
            field="childLock",
            entity_category="config",
            icon="mdi:lock",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_status_child_lock_write,
        ),
        # SelectDesc first — options_field reads this same href live, no
        # static fallback needed (availableModeList is always populated on
        # the one dump seen).
        SelectDesc(
            key="cooking_mode",
            field="mode",
            icon="mdi:tune",
            options_field="availableModeList",
            value_fn=lambda mode: (mode or {}).get("name"),
            write_fn=_status_mode_write,
        ),
        NumberDesc(
            key="power_level",
            field="microwavePowerLevel",
            unit="%",
            native_min=float(POWER_LEVEL_MIN),
            native_max=float(POWER_LEVEL_MAX),
            step=float(POWER_LEVEL_STEP),
            icon="mdi:radar",
            value_fn=lambda v: int_or_none((v or {}).get("setting")),
            write_fn=_power_level_write,
        ),
        NumberDesc(
            key="cook_time",
            field="time",
            unit="s",
            native_min=float(COOK_TIME_MIN_S),
            native_max=float(COOK_TIME_MAX_S),
            step=1.0,
            icon="mdi:timer",
            value_fn=lambda v: int_or_none((v or {}).get("setting")),
            write_fn=_cook_time_write,
        ),
        # subOperation ('ready' alongside operation='ready' on the only
        # dump seen) -- raw diagnostic passthrough, meaning not confirmed
        # beyond mirroring `operation` at idle.
        SensorDesc(
            key="sub_operation",
            field="subOperation",
            entity_category="diagnostic",
        ),
    ),
)

# Per-mode setpoint/power-level bounds and the mode list itself
# (cavityInfo.cavityList[*].modeSpecList) -- see module docstring for why
# this stays a bare coverage marker instead of a live-read source.
MICROWAVE_SPEC = Capability(href="/oven/spec/vs/0")


def _settings_bool_write(field_name):
    """Factory for a single-field on/off PUT against
    /oven/settings/status/vs/0 -- unconfirmed writes, same "flagged guess"
    caveat as the rest of this generation's contracts."""

    def write(p, rep, href=None):
        if p not in ("On", "Off"):
            return None
        return ["oven", "settings", "status", "vs", "0"], {field_name: p.lower()}

    return write


MICROWAVE_SETTINGS = Capability(
    href="/oven/settings/status/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="beep",
            field="beepSound",
            entity_category="config",
            icon="mdi:volume-high",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_settings_bool_write("beepSound"),
        ),
        SwitchDesc(
            key="remind_beep",
            field="remindBeep",
            entity_category="config",
            icon="mdi:bell-ring",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_settings_bool_write("remindBeep"),
        ),
        SwitchDesc(
            key="display_time_auto_sync",
            field="displayTimeAutoSync",
            entity_category="config",
            icon="mdi:clock-sync",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_settings_bool_write("displayTimeAutoSync"),
        ),
        # No supportedWeightUnit/supportedTimeFormat field on this dump to
        # read a select's options from (skill: don't invent a values list)
        # -- read-only enum sensors until one surfaces.
        SensorDesc(
            key="weight_unit",
            field="weightUnit",
            entity_category="diagnostic",
            icon="mdi:scale",
        ),
        SensorDesc(
            key="time_format",
            field="timeFormat",
            entity_category="diagnostic",
            icon="mdi:clock-outline",
        ),
    ),
)

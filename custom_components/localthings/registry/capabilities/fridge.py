"""Capabilities for the refrigerator family (Samsung RF9000B-class).

Resources verified against the dump at local-tools/dumps/10.0.0.254.json.

Temperature unit is read live from each resource, not assumed: the RF9000B
dump reports Fahrenheit, but a TP1X_REF_21K dump (issue #7) reports the same
fields in Celsius for the exact same resources -- the device tells you which
one it is. See `_temp_unit`/`_temp_item_unit` below.

Multi-instance note: the two door resources (/door/cooler/0,
/door/freezer/0) and the two ice-maker resources (/icemaker/one/vs/0,
/icemaker/two/vs/0) use named path segments, so they are modeled via
pattern capabilities that auto-derive distinct entity keys from href
segments.
"""

import datetime

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    TimeDesc,
)
from .common import int_or_none, normalize_temp_unit

# Display names for the beverage zone, flex zone, ice type, and
# ice-making-status enums below live in translations/en.json, keyed by the
# lowercased raw device value.


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _temp_unit(rep):
    """'units': 'C'/'F' (or 'Celsius'/'Fahrenheit') -> '°C'/'°F'. Defaults
    to °F if the device omits the field."""
    return normalize_temp_unit(rep.get("units"))


# Temperature (generic -- covers /temperature/current/* and
# /temperature/desired/*)

TEMP_CURRENT_GENERIC = Capability(
    href=None,
    href_prefix="/temperature/current/",
    strip_prefix_in_key=True,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="temperature",
            field="temperature",
            translation_key="instance_temperature",
            use_instance_name=True,
            icon="mdi:thermometer",
            device_class="temperature",
            unit_fn=_temp_unit,
            state_class="measurement",
        ),
    ),
)


def _temp_setpoint_write(p, rep, href=None, resources=None):
    """Prefer vendor /temperatures/vs/0 when present, else the direct OCF
    /temperature/desired/ write -- on some models only the vendor path
    commits. Item IDs follow the Samsung convention: "0" = Freezer,
    "1" = Fridge/Cooler."""
    if not href:
        return None
    if resources and "/temperatures/vs/0" in resources:
        if "/cooler/" in href:
            item_id = "1"
        elif "/freezer/" in href:
            item_id = "0"
        else:
            return None
        return (
            ["temperatures", "vs", "0"],
            {
                "x.com.samsung.da.items": [
                    {
                        "x.com.samsung.da.id": item_id,
                        "x.com.samsung.da.desired": str(round(float(p))),
                    }
                ]
            },
        )
    return ([s for s in href.strip("/").split("/") if s], {"temperature": round(float(p))})


TEMP_SETPOINT = Capability(
    href=None,
    href_prefix="/temperature/desired/",
    strip_prefix_in_key=True,
    poll_tier="warm",
    entities=(
        NumberDesc(
            key="setpoint",
            field="temperature",
            translation_key="instance_setpoint",
            use_instance_name=True,
            device_class="temperature",
            unit_fn=_temp_unit,
            native_min=-20.0,
            native_max=50.0,
            range_field="range",
            entity_category="config",
            write_fn=_temp_setpoint_write,
        ),
    ),
)

# Discrete cooler setpoint (issue #186): single-door "cooler only" fridges
# report no /temperature/current|desired/* pair, only this vendor resource
# bundling the live desired value with the specific values the unit
# accepts. supportedList (e.g. ['1','2','3','4','7']) is not a contiguous
# range, so this is a select reading its own live options rather than a
# NumberDesc with min/max/step.


def _definite_cooler_write(p, rep, href=None):
    return (
        ["temperature", "definite", "cooler", "vs", "0"],
        {"x.com.samsung.da.definite.desired": p},
    )


DEFINITE_TEMPERATURE_COOLER = Capability(
    href="/temperature/definite/cooler/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="cooler_temperature_setpoint",
            field="x.com.samsung.da.definite.desired",
            icon="mdi:thermometer",
            entity_category="config",
            options_field="x.com.samsung.da.definite.supportedList",
            write_fn=_definite_cooler_write,
        ),
    ),
)


def _definite_freezer_write(p, rep, href=None):
    return (
        ["temperature", "definite", "freezer", "vs", "0"],
        {"x.com.samsung.da.definite.desired": p},
    )


# Freezer half of the same discrete-setpoint pattern (issue #229) -- same
# shape as DEFINITE_TEMPERATURE_COOLER, negative supportedList values.
DEFINITE_TEMPERATURE_FREEZER = Capability(
    href="/temperature/definite/freezer/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="freezer_temperature_setpoint",
            field="x.com.samsung.da.definite.desired",
            icon="mdi:thermometer",
            entity_category="config",
            options_field="x.com.samsung.da.definite.supportedList",
            write_fn=_definite_freezer_write,
        ),
    ),
)

ICEMAKER_NIGHTTIME = Capability(
    href="/icemaker/nighttime/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="ice_night_mode",
            field="ice.night.status",
            icon="mdi:weather-night",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["icemaker", "nighttime", "vs", "0"],
                {"ice.night.status": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Icemaker (generic -- covers /icemaker/one/vs/0, /icemaker/two/vs/0).
# /icemaker/status/vs/0 is an exact-href cap and binds first;
# /icemaker/nighttime/vs/0 is excluded by match_fn (lacks iceMaker.state).
# Entity names interpolate x.com.samsung.da.iceMaker.name ("CUBED_ICE",
# "ICE_BITES") via name_field, not the href's "one"/"two" segment -- these
# two makers can both be enabled at once (issue #27), so they stay
# separate entities rather than one ice-type select.


def _icemaker_write(field):
    return lambda p, rep, href=None: (
        ([s for s in href.strip("/").split("/") if s], {field: p}) if href else None
    )


ICEMAKER_GENERIC = Capability(
    href=None,
    href_prefix="/icemaker/",
    match_fn=lambda rep, resources: "x.com.samsung.da.iceMaker.state" in rep,
    name_field="x.com.samsung.da.iceMaker.name",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="making_status",
            field="x.com.samsung.da.iceMaker.iceMakingStatus",
            use_instance_name=True,
            icon="mdi:cube-outline",
            device_class="enum",
            options=("icestatus_stop", "icestatus_run"),
            translation_key="ice_making_status",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
        SwitchDesc(
            key="enabled",
            field="x.com.samsung.da.iceMaker.state",
            translation_key="instance_enabled",
            use_instance_name=True,
            icon="mdi:cube-outline",
            value_fn=lambda v: v == "On",
            write_fn=_icemaker_write("x.com.samsung.da.iceMaker.state"),
        ),
        SelectDesc(
            key="type",
            field="x.com.samsung.da.iceType.desired",
            use_instance_name=True,
            icon="mdi:cube-outline",
            translation_key="ice_type",
            entity_category="config",
            options_field="x.com.samsung.da.iceType.supported",
            exists_fn=lambda rep, resources: bool(rep.get("x.com.samsung.da.iceType.supported")),
            write_fn=_icemaker_write("x.com.samsung.da.iceType.desired"),
        ),
    ),
)

DOOR_ALERT = Capability(
    href="/settings/sound/alert/door/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="door_alert",
            field="alert.door",
            icon="mdi:bell-alert",
            translation_key="door_alert",
            entity_category="config",
            options_field="supportedAlert.door",
            write_fn=lambda p, rep, href=None: (
                ["settings", "sound", "alert", "door", "vs", "0"],
                {"alert.door": p},
            ),
        ),
    ),
)


# Internal deodorizing filter (issue #318, TP1X_REF_21K). Same
# filterUsage/filterStatus field pair as common.WATER_FILTER, but
# filterUsage here is already a 0-100 percentage with no filterCapacity to
# divide by (confirmed by filterStatus=="wash" at filterUsage=="100") --
# 'air_'-prefixed keys so a fridge with both a water and an air filter gets
# two distinct entities rather than a unique_id collision.
AIR_FILTER = Capability(
    href="/filter/airdustfilter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="air_filter_usage",
            field="x.com.samsung.da.filterUsage",
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="air_filter_status",
            field="x.com.samsung.da.filterStatus",
            device_class="enum",
            options=("normal", "wash", "replace"),
            translation_key="filter_status",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
    ),
)


def _status_lock_write(field):
    return lambda p, rep, href=None: (
        ["status", "lock", "vs", "0"],
        {field: "On" if p == "On" else "Off"},
    )


STATUS_LOCK = Capability(
    href="/status/lock/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="auto_door_opener",
            field="x.com.samsung.da.ado.devicecontrol",
            icon="mdi:door-open",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_status_lock_write("x.com.samsung.da.ado.devicecontrol"),
        ),
        SwitchDesc(
            key="fridge_sound",
            field="x.com.samsung.da.device.sound",
            icon="mdi:volume-high",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_status_lock_write("x.com.samsung.da.device.sound"),
        ),
        # Auto Door Open's own voice/sound feedback toggles (issue #328,
        # TP1X_REF_21K family) -- siblings of auto_door_opener above, not
        # duplicates of fridge_sound (device.sound is the general appliance
        # beep, these two gate ado's own prompts). Only seen on the
        # auto-door-equipped variants (single/kimchi/winecellar), not the
        # earlier TP1X_REF_21K dumps that predate that feature -- gated on
        # each field's own presence rather than assumed universal.
        SwitchDesc(
            key="auto_door_voice_control",
            field="x.com.samsung.da.ado.voicecontrol",
            icon="mdi:microphone",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_status_lock_write("x.com.samsung.da.ado.voicecontrol"),
            exists_fn=lambda rep, resources: "x.com.samsung.da.ado.voicecontrol" in rep,
        ),
        SwitchDesc(
            key="auto_door_sound_control",
            field="x.com.samsung.da.ado.soundcontrol",
            icon="mdi:volume-medium",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_status_lock_write("x.com.samsung.da.ado.soundcontrol"),
            exists_fn=lambda rep, resources: "x.com.samsung.da.ado.soundcontrol" in rep,
        ),
    ),
)

# Auto Door Open's paired delay setting (issue #328, TP1X_REF_21K family):
# how long the door stays held open before it re-closes, on/off itself is
# STATUS_LOCK.auto_door_opener above. Same discrete-options-select shape as
# DEFINITE_TEMPERATURE_COOLER/FREEZER. Unit unconfirmed -- no supportedList
# field states it and the report carried no app screenshot -- so this is a
# raw-code select rather than a guessed seconds/minutes NumberDesc.


def _auto_door_timer_write(p, rep, href=None):
    return ["autodoor", "timer", "vs", "0"], {"x.com.samsung.da.time.desired": p}


AUTO_DOOR_TIMER = Capability(
    href="/autodoor/timer/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="auto_door_timer",
            field="x.com.samsung.da.time.desired",
            icon="mdi:timer-outline",
            translation_key="auto_door_timer",
            entity_category="config",
            options_field="x.com.samsung.da.time.supportedOptions",
            write_fn=_auto_door_timer_write,
        ),
    ),
)

# /autodoor/<variant>/vs/0 -- one per fridge sub-type sharing the Auto Door
# Open feature (single-door, kimchi, winecellar seen so far; issue #328).
# Each reports only x.com.samsung.da.ado.openOptions, declaring which open
# styles that variant supports -- every dump seen so far carries exactly
# one option ('Single') with no paired desired/current field to make a
# choice against, the same "no real choice to expose yet" shape as
# ignored.py's /mode/0. Bound with no entities to record coverage; revisit
# if a device ever reports more than one option.
#
# A pattern cap rather than one entry per variant: match_fn (not just the
# prefix) is what actually gates this, so a future variant href needs no
# code change to stay covered, and /autodoor/timer/vs/0's own exact-href
# AUTO_DOOR_TIMER above always wins for that href regardless (discover()
# only falls through to pattern caps when no exact cap matched). This is
# registry-scoped, not global -- unlike ignored.IGNORED, the unknown-
# device-type fallback never reaches it, so the prefix caveat in
# ignored.py's own docstring doesn't apply here.
AUTO_DOOR_VARIANT = Capability(
    href=None,
    href_prefix="/autodoor/",
    match_fn=lambda rep, resources: "x.com.samsung.da.ado.openOptions" in rep,
)

# Wine-cellar variant (x.com.st.d.winecellar, issue #328) of the same
# deodorizing filter AIR_FILTER models -- filterUsage/filterStatus at a
# different href, same 0-100-percentage-already shape (see AIR_FILTER's own
# comment). filterUsage reads '-1' on the only dump seen (filterStatus
# 'normal'), relayed as-is rather than special-cased -- no second dump to
# confirm whether that's a real sentinel or this unit just not tracking it.
# Own 'deodor_'-prefixed keys rather than reusing AIR_FILTER.entities
# verbatim -- same collision AIR_FILTER's own 'air_' prefix was chosen to
# avoid against WATER_FILTER, and both filters are plausible on one unit
# (this device's own board reports an internal air filter on other
# TP1X_REF_21K variants).
DEODOR_FILTER = Capability(
    href="/filter/deodorfilter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="deodor_filter_usage",
            field="x.com.samsung.da.filterUsage",
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="deodor_filter_status",
            field="x.com.samsung.da.filterStatus",
            device_class="enum",
            options=("normal", "wash", "replace"),
            translation_key="filter_status",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
    ),
)

# Wine-cellar multi-compartment pantry select (issue #328): same
# mode/supportedOptions shape as PANTRY_ZONE, at its own href with a 5-way
# option set (Processed_Meat/Cheese/Nuts/Fruit/Wine) instead of PANTRY_ZONE's.
# Only a "one" instance seen -- not generalized to a pattern cap, same
# discipline as PANTRY_ZONE itself.


def _winecellar_pantry_write(p, rep, href=None):
    return ["status", "winecellar", "pantry", "one", "vs", "0"], {"x.com.samsung.da.mode": p}


WINECELLAR_PANTRY_ZONE = Capability(
    href="/status/winecellar/pantry/one/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="winecellar_pantry_zone_mode",
            field="x.com.samsung.da.mode",
            icon="mdi:glass-wine",
            translation_key="winecellar_pantry_zone_mode",
            entity_category="config",
            options_field="x.com.samsung.da.supportedOptions",
            write_fn=_winecellar_pantry_write,
        ),
    ),
)

# Wine-cellar internal table-revision marker (issue #328) -- versioning
# metadata, not appliance state. Same treatment as ignored.py's
# /wm/setinfo/vs/0.
WINECELLAR_INFO = Capability(href="/information/winecellar/vs/0")

# /defrost/delay/vs/0 is the writable toggle to postpone a scheduled
# defrost. /defrost/block/vs/0 is unrelated: despite the "block" naming,
# live dumps confirm DEFROST_BLOCK_ON means the defrost cycle is actively
# running right now (seen with defrost_delay off) -- "block" refers to the
# evaporator/coil block being defrosted, not a prevention state.

DEFROST_DELAY = Capability(
    href="/defrost/delay/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="defrost_delay",
            field="x.com.samsung.da.delayDefrost",
            icon="mdi:snowflake-off",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["defrost", "delay", "vs", "0"],
                {"x.com.samsung.da.delayDefrost": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# OCF-native boolean mirror of DEFROST_DELAY -- only the vendor resource
# above has a confirmed write contract, so bind this without another
# entity to record it as an intentional duplicate.
DEFROST_DELAY_NATIVE_DUPLICATE = Capability(
    href="/defrost/delay/0",
)

DEFROST_BLOCK_STATUS = Capability(
    href="/defrost/block/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="defrost_active",
            field="x.com.samsung.da.modes",
            icon="mdi:snowflake-melt",
            entity_category="diagnostic",
            value_fn=lambda modes: bool(modes) and modes[0] == "DEFROST_BLOCK_ON",
        ),
    ),
)


def _refrigeration_write(field_name):
    def _write(p, rep, href=None):
        if p not in ("On", "Off"):
            return None
        return ["refrigeration", "vs", "0"], {field_name: p}

    return _write


REFRIGERATION = Capability(
    href="/refrigeration/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="rapid_fridge",
            field="x.com.samsung.da.rapidFridge",
            icon="mdi:fridge-industrial",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_refrigeration_write("x.com.samsung.da.rapidFridge"),
        ),
        SwitchDesc(
            key="rapid_freezing",
            field="x.com.samsung.da.rapidFreezing",
            icon="mdi:snowflake",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_refrigeration_write("x.com.samsung.da.rapidFreezing"),
        ),
    ),
)


def _autofill_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["autofill", "vs", "0"], {"x.com.samsung.da.autofill": p}


AUTOFILL = Capability(
    href="/autofill/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="autofill",
            field="x.com.samsung.da.autofill",
            icon="mdi:cup-water",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_autofill_write,
        ),
    ),
)


_PROXIMITY_LEVELS = {
    "0": "nearest",
    "1": "near",
    "2": "middle",
    "3": "far",
}
_PROXIMITY_RAW_BY_LEVEL = {level: raw for raw, level in _PROXIMITY_LEVELS.items()}


def _proximity_level(value):
    return _PROXIMITY_LEVELS.get(str(value))


def _proximity_options(resources):
    rep = resources.get("/proximity/vs/0") or {}
    supported = rep.get("supportedLevels")
    if not isinstance(supported, (list, tuple)):
        return []
    return [str(value) for value in supported if _proximity_level(value) is not None]


def _proximity_display(value, _resources):
    return _proximity_level(value)


def _proximity_write(value, rep, href=None):
    value = str(value)
    raw = value if value in _PROXIMITY_LEVELS else _PROXIMITY_RAW_BY_LEVEL.get(value.casefold())
    supported_levels = rep.get("supportedLevels")
    if not isinstance(supported_levels, (list, tuple)):
        return None
    supported = {str(item) for item in supported_levels}
    if raw is None or raw not in supported:
        return None
    return ["proximity", "vs", "0"], {"currentLevel": raw}


def _proximity_setting_exists(rep, resources):
    return is_stub_rep(rep) or (
        _proximity_level(rep.get("currentLevel")) is not None
        and bool(_proximity_options(resources))
    )


def _proximity_sense_exists(rep, _resources):
    return is_stub_rep(rep) or _proximity_level(rep.get("desiredSenseLevel")) is not None


def _proximity_sense_options(resources):
    rep = resources.get("/proximity/vs/0") or {}
    supported = rep.get("supportedSenseLevels")
    if not isinstance(supported, (list, tuple)):
        return []
    return [level for value in supported if (level := _proximity_level(value)) is not None]


WELCOME_LIGHTING = Capability(
    href="/proximity/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="welcome_lighting",
            field="status",
            icon="mdi:motion-sensor",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["proximity", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="welcome_lighting_proximity",
            field="currentLevel",
            icon="mdi:motion-sensor",
            entity_category="config",
            options=_proximity_options,
            display_fn=_proximity_display,
            exists_fn=_proximity_setting_exists,
            value_fn=_proximity_level,
            write_fn=_proximity_write,
        ),
        # desiredSenseLevel freezes while welcome lighting is off, so it is
        # firmware diagnostics rather than a live motion-detection entity.
        SensorDesc(
            key="welcome_lighting_sense_level",
            field="desiredSenseLevel",
            icon="mdi:motion-sensor",
            entity_category="diagnostic",
            enabled_default=False,
            device_class="enum",
            options=_proximity_sense_options,
            exists_fn=_proximity_sense_exists,
            value_fn=_proximity_level,
        ),
    ),
)

# Enhanced cabinet light nighttime schedule: night.starttime is an ISO
# datetime (only the time portion matters), night.duration.minute is the
# window length. End time is derived so both time entities write back to
# the same resource without stepping on each other: writing start
# preserves duration; writing end recalculates it.

_NIGHT_BRIGHTNESS_OPTIONS = ("33", "66", "100")


def _tz_offset(rep) -> datetime.timedelta:
    s = rep.get("timezone.offset", "+00:00")
    try:
        sign = 1 if s[0] == "+" else -1
        h, m = map(int, s[1:].split(":"))
        return datetime.timedelta(hours=h, minutes=m) * sign
    except (ValueError, TypeError, IndexError):
        return datetime.timedelta(0)


def _parse_night_time(iso_str, offset=datetime.timedelta(0)) -> "datetime.time | None":
    """Convert a UTC ISO datetime string to local time using offset."""
    if not iso_str:
        return None
    try:
        return (datetime.datetime.fromisoformat(iso_str) + offset).time().replace(second=0)
    except (ValueError, TypeError):
        return None


def _night_start_value(rep) -> "datetime.time | None":
    return _parse_night_time(rep.get("night.starttime"), _tz_offset(rep))


def _night_end_value(rep) -> "datetime.time | None":
    start_t = _parse_night_time(rep.get("night.starttime"), _tz_offset(rep))
    duration_str = rep.get("night.duration.minute")
    if start_t is None or duration_str is None:
        return None
    try:
        duration = int(duration_str)
    except (ValueError, TypeError):
        return None
    return (
        (
            datetime.datetime.combine(datetime.date.today(), start_t)
            + datetime.timedelta(minutes=duration)
        )
        .time()
        .replace(second=0)
    )


def _night_start_write(p, rep, href=None):
    # p is local time; subtract offset to get UTC for storage
    offset = _tz_offset(rep)
    utc_dt = datetime.datetime.combine(datetime.date.today(), p) - offset
    old_iso = rep.get("night.starttime") or ""
    try:
        new_dt = datetime.datetime.fromisoformat(old_iso).replace(
            hour=utc_dt.hour, minute=utc_dt.minute, second=0
        )
    except (ValueError, TypeError):
        new_dt = utc_dt
    return ["cabinet", "light", "enhanced", "vs", "0"], {
        "night.starttime": new_dt.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _night_end_write(p, rep, href=None):
    # duration = end_local - start_local; offset cancels so work in local time
    start_t = _parse_night_time(rep.get("night.starttime"), _tz_offset(rep))
    if start_t is None:
        return None
    start_min = start_t.hour * 60 + start_t.minute
    end_min = p.hour * 60 + p.minute
    duration = (end_min - start_min) % (24 * 60)
    return ["cabinet", "light", "enhanced", "vs", "0"], {
        "night.duration.minute": str(duration),
    }


def _night_lighting_schedule_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["cabinet", "light", "enhanced", "vs", "0"], {
        "light.control.status": p,
    }


CABINET_LIGHT_ENHANCED = Capability(
    href="/cabinet/light/enhanced/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="night_lighting_schedule",
            field="light.control.status",
            icon="mdi:weather-night",
            entity_category="config",
            exists_fn=lambda rep, resources: is_stub_rep(rep) or "light.control.status" in rep,
            value_fn=lambda v: v == "On",
            write_fn=_night_lighting_schedule_write,
        ),
        SelectDesc(
            key="day_brightness",
            field="level.brightness.daytime",
            icon="mdi:brightness-5",
            translation_key="day_brightness",
            entity_category="config",
            options=_NIGHT_BRIGHTNESS_OPTIONS,
            write_fn=lambda p, rep, href=None: (
                ["cabinet", "light", "enhanced", "vs", "0"],
                {"level.brightness.daytime": p},
            ),
        ),
        SelectDesc(
            key="brightness_level",
            field="level.brightness.nighttime",
            icon="mdi:brightness-4",
            translation_key="brightness_level",
            entity_category="config",
            options=_NIGHT_BRIGHTNESS_OPTIONS,
            write_fn=lambda p, rep, href=None: (
                ["cabinet", "light", "enhanced", "vs", "0"],
                {"level.brightness.nighttime": p},
            ),
        ),
        TimeDesc(
            key="night_start",
            field="",
            icon="mdi:clock-start",
            entity_category="config",
            rep_fn=_night_start_value,
            write_fn=_night_start_write,
        ),
        TimeDesc(
            key="night_end",
            field="",
            icon="mdi:clock-end",
            entity_category="config",
            rep_fn=_night_end_value,
            write_fn=_night_end_write,
        ),
    ),
)


def _cabinet_light_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["cabinet", "light", "total", "vs", "0"], {
        "x.com.samsung.da.lightControl": p,
    }


CABINET_LIGHT = Capability(
    href="/cabinet/light/total/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="cabinet_light_switch",
            field="x.com.samsung.da.lightControl",
            icon="mdi:fridge-outline",
            value_fn=lambda v: v == "On",
            write_fn=_cabinet_light_write,
        ),
        SwitchDesc(
            key="cabinet_light_dim",
            field="light.dimming.status",
            icon="mdi:brightness-auto",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["cabinet", "light", "total", "vs", "0"],
                {"light.dimming.status": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)


def _sabbath_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["sabbath", "vs", "0"], {"x.com.samsung.da.sabbathMode": p}


SABBATH = Capability(
    href="/sabbath/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="sabbath_mode",
            field="x.com.samsung.da.sabbathMode",
            icon="mdi:hands-pray",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_sabbath_write,
        ),
    ),
)


def _bzone_write(p, rep, href=None):
    return ["specialzone", "one", "vs", "0"], {"roomDesiredMode": p}


BEVERAGE_ZONE = Capability(
    href="/specialzone/one/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="beverage_zone_mode",
            field="roomDesiredMode",
            icon="mdi:glass-wine",
            translation_key="beverage_zone_mode",
            entity_category="config",
            options_field="roomSupportedModes",
            write_fn=_bzone_write,
        ),
    ),
)

# Pantry / Cool Select Zone -- a convertible compartment toggled between
# wine/deli/drinks presets (issue #20). Same shape as BEVERAGE_ZONE but a
# distinct field set (x.com.samsung.da.mode/supportedOptions vs
# roomDesiredMode/roomSupportedModes). Only a "one" instance seen; not
# generalized to a pattern cap until a second instance turns up.


def _pantry_write(p, rep, href=None):
    return ["status", "pantry", "one", "vs", "0"], {"x.com.samsung.da.mode": p}


PANTRY_ZONE = Capability(
    href="/status/pantry/one/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="pantry_zone_mode",
            field="x.com.samsung.da.mode",
            icon="mdi:glass-wine",
            translation_key="pantry_zone_mode",
            entity_category="config",
            options_field="x.com.samsung.da.supportedOptions",
            write_fn=_pantry_write,
        ),
    ),
)

# Flex zone (convertible drawer -- /mode/vs/0 on RF9000-class fridges):
# x.com.samsung.da.modes holds several orthogonal flags in one list; the
# flex-zone entry is whichever item also appears in supportedOptions (the
# other flags, WATERFILTER_*/DEFROST_BLOCK_*/CVN_*_ZONE, never do). The
# prefix on that item varies by family (CV_TTYPE_RF9000A_ vs CV_FDR_ on
# Bespoke, issues #27/#26), so match by list membership instead of a
# hardcoded prefix. Write replaces only that item.


def _flex_zone_supported(rep):
    return set(rep.get("x.com.samsung.da.supportedOptions") or ())


def _flex_zone_current(rep):
    # Every dump seen has at most one modes/supportedOptions overlap; a
    # future device reporting two would read the first and the write below
    # would drop both.
    modes = rep.get("x.com.samsung.da.modes") or []
    supported = _flex_zone_supported(rep)
    return next((m for m in modes if m in supported), None)


def _flex_zone_write(p, rep, href=None):
    supported = _flex_zone_supported(rep)
    modes = [m for m in (rep.get("x.com.samsung.da.modes") or []) if m not in supported]
    modes.append(p)
    return ["mode", "vs", "0"], {"x.com.samsung.da.modes": modes}


FLEX_ZONE = Capability(
    href="/mode/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="flex_zone_mode",
            icon="mdi:thermostat",
            translation_key="flex_zone_mode",
            entity_category="config",
            options_field="x.com.samsung.da.supportedOptions",
            # A nonempty supportedOptions alone isn't sufficient: the
            # kimchi-refrigerator family (issue #26) also populates both
            # fields, but its tokens carry a "_[n]:[n]" suffix on
            # supportedOptions that modes never repeats, so nothing ever
            # overlaps there. Require an actual resolvable value so this
            # stays absent on that family instead of stuck on "unknown".
            exists_fn=lambda rep, resources: _flex_zone_current(rep) is not None,
            rep_fn=_flex_zone_current,
            write_fn=_flex_zone_write,
        ),
    ),
)

# Generic door pattern capability (href=None -- use as pattern_cap only)


def _door_open_state(rep):
    """Most /door/* resources report bare `openState`, but the
    ARTIK051_DONGLE_REF family's /door/onedoorfreezer/vs/0 (issues #77,
    #83) reports `x.com.samsung.da.openState` instead -- check both."""
    v = rep.get("openState")
    if v is None:
        v = rep.get("x.com.samsung.da.openState")
    return v == "Open"


DOOR_GENERIC = Capability(
    href=None,
    href_prefix="/door/",
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="open",
            rep_fn=_door_open_state,
            translation_key="instance_open",
            use_instance_name=True,
            device_class="door",
        ),
    ),
)

# Kimchi refrigerator compartments (TP2X_REF_20K-class 3-compartment
# units, issue #26): top/middle/bottom each report their own storage mode
# plus a ripening status/timer on /status/kimchi/<slot>/vs/0, modeled as a
# pattern capability the same way DOOR_GENERIC is. Only the top
# compartment's door is reported separately (kimchidoors); middle/bottom
# apparently have no contact switch, hence the narrower KIMCHI_DOOR_GENERIC
# below rather than assuming it's universal.
#
# The same state is also packed into single tokens (e.g.
# "KIMCHIT_KIMCHI_STORAGE_NORMAL") on /mode/vs/0, the resource FLEX_ZONE
# reads for RF9000-class fridges -- this binds to /status/kimchi/<slot>/
# vs/0's plain, self-describing currentMode/supportMode instead.
#
# Write path is unconfirmed on real hardware; same "write the field back to
# the entity's own href" convention as PANTRY_ZONE/BEVERAGE_ZONE.
#
# translations/en.json's kimchi_zone_mode labels were translated directly
# from the reporter's own Korean SmartThings app screenshots (not guessed),
# and cross-checked against supportMode order to confirm the on-screen
# option order matches the array order throughout -- so COLD/WARM
# consistently means Strong/Weak everywhere that suffix appears.


def _kimchi_mode_write(p, rep, href=None):
    if not href or p not in (rep.get("x.com.samsung.da.supportMode") or ()):
        return None
    return [s for s in href.strip("/").split("/") if s], {
        "x.com.samsung.da.currentMode": p,
    }


KIMCHI_ZONE = Capability(
    href=None,
    href_prefix="/status/kimchi/",
    strip_prefix_in_key=True,
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="mode",
            field="x.com.samsung.da.currentMode",
            use_instance_name=True,
            icon="mdi:fridge-outline",
            translation_key="kimchi_zone_mode",
            entity_category="config",
            options_field="x.com.samsung.da.supportMode",
            write_fn=_kimchi_mode_write,
        ),
        SensorDesc(
            key="ripening_status",
            field="x.com.samsung.da.ripeStatus",
            use_instance_name=True,
            icon="mdi:progress-clock",
            translation_key="kimchi_ripening_status",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="ripening_remaining",
            field="x.com.samsung.da.ripeRemaintime",
            use_instance_name=True,
            icon="mdi:timer-sand",
            translation_key="kimchi_ripening_remaining",
            entity_category="diagnostic",
            # No dump has this nonzero (ripeStatus is always "Off" so far)
            # -- unit unconfirmed, so this stays a bare number.
            value_fn=_int,
        ),
        SensorDesc(
            key="rack_count",
            field="x.com.samsung.da.rackCount",
            use_instance_name=True,
            icon="mdi:tray-full",
            translation_key="kimchi_rack_count",
            entity_category="diagnostic",
            enabled_default=False,
            value_fn=_int,
        ),
    ),
)

KIMCHI_DOOR_GENERIC = Capability(
    href=None,
    href_prefix="/kimchidoors/",
    strip_prefix_in_key=True,
    poll_tier="hot",
    entities=(
        # Not deduped against DOORS_FALLBACK below: on the one reporter
        # this binds alongside, /doors/vs/0's aggregate carries a single
        # generic item (id "4", no /door/<instance> siblings) that doesn't
        # share this compartment's "top" numbering -- a distinct
        # main-cabinet door, not this drawer's contact switch reported
        # twice.
        BinarySensorDesc(
            key="open",
            rep_fn=_door_open_state,
            translation_key="instance_open",
            use_instance_name=True,
            device_class="door",
        ),
    ),
)

# Aggregate-resource fallbacks: /doors/vs/0, /temperatures/vs/0, and
# /icemaker/status/vs/0 each duplicate information the per-instance hrefs
# above expose more precisely, on hardware that has them -- not every
# fridge does. Each fallback's match_fn checks for the richer sibling
# hrefs and only binds when they're absent, so it's a no-op wherever the
# richer hrefs exist and a real (coarser) source where they don't.


def _any_door_generic(resources):
    return any(h.startswith("/door/") for h in resources)


DOORS_FALLBACK = Capability(
    href="/doors/vs/0",
    match_fn=lambda rep, resources: not _any_door_generic(resources),
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="door_open",
            field="x.com.samsung.da.items",
            device_class="door",
            value_fn=lambda items: any(
                i.get("x.com.samsung.da.openState") == "Open" for i in (items or [])
            ),
        ),
    ),
)


def _any_temperature_generic(resources):
    return any(h.startswith("/temperature/") for h in resources)


def _temp_item_value(items, keyword):
    for item in items or []:
        if keyword.lower() in (item.get("x.com.samsung.da.description") or "").lower():
            return _int(item.get("x.com.samsung.da.current"))
    return None


def _temp_item_unit(items, keyword):
    for item in items or []:
        if keyword.lower() in (item.get("x.com.samsung.da.description") or "").lower():
            return normalize_temp_unit(item.get("x.com.samsung.da.unit"))
    return "°F"


TEMPERATURES_FALLBACK = Capability(
    href="/temperatures/vs/0",
    match_fn=lambda rep, resources: not _any_temperature_generic(resources),
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="freezer_temperature",
            field="x.com.samsung.da.items",
            icon="mdi:thermometer",
            device_class="temperature",
            state_class="measurement",
            unit_fn=lambda rep: _temp_item_unit(rep.get("x.com.samsung.da.items"), "Freezer"),
            value_fn=lambda items: _temp_item_value(items, "Freezer"),
        ),
        SensorDesc(
            key="fridge_temperature",
            field="x.com.samsung.da.items",
            icon="mdi:thermometer",
            device_class="temperature",
            state_class="measurement",
            unit_fn=lambda rep: _temp_item_unit(rep.get("x.com.samsung.da.items"), "Fridge"),
            value_fn=lambda items: _temp_item_value(items, "Fridge"),
        ),
    ),
)


def _any_icemaker_unit_generic(resources):
    return any(
        h.startswith("/icemaker/")
        and isinstance(r, dict)
        and "x.com.samsung.da.iceMaker.state" in r
        for h, r in resources.items()
    )


ICEMAKER_STATUS_FALLBACK = Capability(
    href="/icemaker/status/vs/0",
    match_fn=lambda rep, resources: not _any_icemaker_unit_generic(resources),
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="ice_maker_enabled",
            field="x.com.samsung.da.iceMaker",
            icon="mdi:cube-outline",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["icemaker", "status", "vs", "0"],
                {"x.com.samsung.da.iceMaker": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# OCF-native aggregate mirror of ICEMAKER_STATUS_FALLBACK. On the captured
# TP1X_REF_21K it duplicates both the vendor aggregate and the richer
# per-unit hrefs; its write contract isn't advertised, so bind it as a
# duplicate only.
ICEMAKER_STATUS_NATIVE_DUPLICATE = Capability(
    href="/icemaker/status/0",
)

# OCF-native /refrigeration/0 (issue #7): its three fields duplicate two
# different richer hrefs (REFRIGERATION's rapidFridge/rapidFreezing,
# DEFROST_BLOCK_STATUS's defrost_active), each absent independently, so
# gating is per-entity (exists_fn) rather than one capability-level
# match_fn. No write path confirmed, so these stay read-only.
REFRIGERATION_FALLBACK = Capability(
    href="/refrigeration/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="defrost_active",
            field="defrost",
            icon="mdi:snowflake-melt",
            entity_category="diagnostic",
            value_fn=lambda v: bool(v),
            exists_fn=lambda rep, resources: "/defrost/block/vs/0" not in resources,
        ),
        BinarySensorDesc(
            key="rapid_fridge",
            field="rapidCool",
            icon="mdi:fridge-industrial",
            entity_category="diagnostic",
            value_fn=lambda v: bool(v),
            exists_fn=lambda rep, resources: "/refrigeration/vs/0" not in resources,
        ),
        BinarySensorDesc(
            key="rapid_freezing",
            field="rapidFreeze",
            icon="mdi:snowflake",
            entity_category="diagnostic",
            value_fn=lambda v: bool(v),
            exists_fn=lambda rep, resources: "/refrigeration/vs/0" not in resources,
        ),
    ),
)

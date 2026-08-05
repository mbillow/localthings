"""Capabilities for the Samsung air-conditioner family (ARTIK051_PRAC-class,
issue #17 / ARTIK051_PRAC_20K).

Core controls (power, mode, temperature, fan, swing, preset) surface as one
composite HA `climate` entity; climate.py reads the sibling resources bound
here off the coordinator snapshot. These caps stay out of the global
`ALL`/`CAPABILITIES`: several hrefs (`/mode/vs/0`, `/temperatures/vs/0`,
`/humidity/*`) collide with other families' schemas (see
capabilities/__init__.py) -- AC-only, by_type registry only.
"""

from dataclasses import replace

from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    ClimateDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from . import common
from .common import filter_usage_percent, normalize_temp_unit
from .laundry import option_write


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _beep_on(rep):
    """Beep on/off from the `Volume_*` option token (`Volume_Mute` = off,
    else on)."""
    tok = _option_token(rep, "Volume")
    if tok is None:
        return None
    return tok != "Mute"


def _beep_write(payload, rep, href=None):
    """Toggle beep via a single-token options write (a full options RMW
    reverts on ARTIK051_PRAC). 'On' restores the last non-Mute level rather
    than forcing Volume_100, so a user's intermediate setting survives an
    off/on cycle; falls back to 100 when no prior level is known."""
    if payload not in ("On", "Off"):
        return None
    if payload == "Off":
        token = "Mute"
    else:
        prev = _option_token(rep, "Volume")
        token = prev if (prev and prev != "Mute") else "100"
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Volume", token),
    }


def _tropical_night_value(rep):
    """Tropical night mode level (0-16) from the `Sleep_<N>` option token."""
    tok = _option_token(rep, "Sleep")
    if tok is None:
        return None
    return _int(tok)


def _tropical_night_write(value, rep, href=None):
    """Set tropical night level via a single-token `Sleep_<N>` write.
    Cloud counterpart: custom.airConditionerTropicalNightMode."""
    try:
        level = round(float(value))
    except (TypeError, ValueError):
        return None
    if not 0 <= level <= 16:
        return None
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Sleep", str(level)),
    }


def _filter_unit(rep):
    """Filter-usage unit, normalized from filterCapacityUnit ('Hour' -> 'h')."""
    u = rep.get("x.com.samsung.da.filterCapacityUnit")
    return {"Hour": "h", "Minute": "min", "Second": "s"}.get(u, u or "h")


def _threshold_write(payload, rep, href=None):
    """filterDesiredUsage is locally writable via a plain scalar POST
    (confirmed live on ARTIK051_PRAC). The Select only surfaces where the
    device advertises supportedFilterDesiredUsage, so options are known
    rather than guessed."""
    return ["filter", "airdustfilter", "vs", "0"], {
        "x.com.samsung.da.filterDesiredUsage": payload,
    }


def _sensor_item_value(items, type_):
    """First value of the /sensors/vs/0 item with the given
    x.com.samsung.da.type. Dust/FineDust/SuperFineDust report a 2-element
    array; only v[0] is used, since the second element's meaning is
    unconfirmed. No device_class is set: the resource exposes no unit."""
    for it in items or []:
        if isinstance(it, dict) and it.get("x.com.samsung.da.type") == type_:
            v = it.get("x.com.samsung.da.value")
            if isinstance(v, list) and v:
                return str(v[0])
            return None
    return None


def _has_sensor_type(type_):
    """True when /sensors/vs/0's items[] lists an item of this type.

    This only proves the type is *listed*, not that the reading is real:
    issue #166 (ARTIK051_PRAC_20K) lists all five types with permanent-zero
    values on units the reporter confirmed don't have the hardware. So
    entities gated on this stay disabled by default (see AIR_QUALITY) rather
    than existence-gated further, to avoid silently dropping real readings
    on hardware not yet seen.
    """

    def fn(rep, resources):
        return any(
            isinstance(i, dict) and i.get("x.com.samsung.da.type") == type_
            for i in (rep.get("x.com.samsung.da.items") or [])
        )

    return fn


# Canonical AC resource hrefs. climate.py binds HREF_MODE and reads the
# CLIMATE_CONSUMED_HREFS siblings off the coordinator snapshot; declared once
# here so climate.py and the coverage list below can't drift out of sync.
HREF_MODE = "/mode/vs/0"  # primary, bound by CLIMATE
HREF_POWER = "/power/0"  # OCF on/off
HREF_POWER_VS = "/power/vs/0"  # vendor fallback for on/off
HREF_TEMP_CURRENT = "/temperature/current/0"
HREF_TEMP_DESIRED = "/temperature/desired/0"
HREF_TEMP_CONTROL = "/temperature/control/vs/0"  # target_temperature_step
HREF_WIND_STRENGTH = "/wind/strength/vs/0"  # fan_mode
HREF_WIND_DIRECTION = "/wind/direction/vs/0"  # swing_mode
# WindFree boards (issue #126) have no HREF_WIND_DIRECTION and instead carry a
# 2-axis oscillation resource; climate.py falls back to this when absent.
HREF_WIND_OSCILLATION = "/wind/oscillation/vs/0"  # swing_mode fallback
HREF_CONVENIENT = "/mode/convenient/vs/0"  # preset_mode
HREF_TEMPS_VS = "/temperatures/vs/0"  # vendor temp fallback (items[] array)
# Legacy ARTIK051 boards (issue #136) have no /wind/* resources: fan speed and
# vane direction live together here instead. See climate.py's _legacy_airflow.
HREF_AIRFLOW = "/airflow/vs/0"  # legacy fan_mode + swing_mode

CLIMATE_CONSUMED_HREFS = [
    HREF_POWER,
    HREF_POWER_VS,
    HREF_TEMP_CURRENT,
    HREF_TEMP_DESIRED,
    HREF_TEMP_CONTROL,
    HREF_TEMPS_VS,
    HREF_WIND_STRENGTH,
    HREF_WIND_DIRECTION,
    HREF_WIND_OSCILLATION,
    HREF_CONVENIENT,
    HREF_AIRFLOW,
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _temps_vs_item(rep):
    """First item of the vendor `/temperatures/vs/0` items[] array -- the
    Tizen Lite board's only current-temperature source. Duplicated from
    climate.py's identical helper to avoid a capabilities<->platform import
    cycle."""
    items = rep.get("x.com.samsung.da.items")
    if isinstance(items, (list, tuple)) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _temps_vs_current(rep):
    return _num(_temps_vs_item(rep).get("x.com.samsung.da.current"))


def _temps_vs_unit(rep):
    return normalize_temp_unit(_temps_vs_item(rep).get("x.com.samsung.da.unit"), "°C")


def _first_mode(rep):
    """Representative scalar for the flattened golden state; the real
    climate entity derives hvac_mode from power + mode instead."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


def _mode_options(rep):
    opts = rep.get("x.com.samsung.da.options")
    return opts if isinstance(opts, (list, tuple)) else ()


def _has_display_light_option(rep, resources):
    """True when the panel light lives in /mode/vs/0's `Light_*` option
    token rather than a dedicated /light/vs/0 switch -- the two encodings
    are mutually exclusive across observed boards."""
    return any(isinstance(o, str) and o.startswith("Light_") for o in _mode_options(rep))


def _display_light_on(rep):
    """Panel light state from /mode/vs/0's options. The token is INVERTED:
    a live toggle test showed `Light_Off` while lit and `Light_On` while
    dark (the flag really means "night/display-off mode active")."""
    for o in _mode_options(rep):
        if isinstance(o, str) and o.startswith("Light_"):
            return o == "Light_Off"
    return None


def _display_light_write(payload, rep, href=None):
    """Toggle the panel light via a single-token options write. Polarity is
    inverted (see _display_light_on): ON writes 'Light_Off', OFF writes
    'Light_On'."""
    token = "Off" if payload == "On" else "On"
    return (["mode", "vs", "0"], {"x.com.samsung.da.options": option_write("Light", token)})


# Legacy ARTIK051 boards keep several settings that newer boards expose as
# their own resources (/option/*, /electriccurrent/vs/0, ...) as
# `<Prefix>_<value>` tokens in /mode/vs/0's options instead. Reads pull the
# token apart; writes reuse the same single-token merge as the display light.


def _option_token(rep, prefix):
    """Value part of a `<prefix>_<value>` token in /mode/vs/0's options."""
    for option in _mode_options(rep):
        if isinstance(option, str) and option.startswith(prefix + "_"):
            return option.split("_", 1)[1]
    return None


def is_legacy_board(resources):
    """True for the board generation whose airflow lives in /airflow/vs/0
    rather than /wind/strength/vs/0 -- every AC dump on record has one shape
    or the other. Same test as climate.py's _legacy_airflow(), so the
    entities below and the climate entity can't disagree about generation."""
    return HREF_AIRFLOW in resources and HREF_WIND_STRENGTH not in resources


# Legacy ARTIK051 boards (issue #193, ARTIK051_KRAC_18K) report
# /energy/consumption/vs/0's cumulativePower in centiwatt-hours -- 100x the
# plain Wh every other board family (and common.wh_to_kwh) assumes. Confirmed
# against the reporter's own SmartThings-app reading: raw 117430000 vs the
# app's 1,174.30 kWh is exactly a /100000 factor.
def _legacy_cumulative_power_kwh(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(n / 100000.0, 2)


# ---------------------------------------------------------------------------
# What an error code means, in the appliance's own words.
#
# /alarms/vs/0 carries the code as `ErrorCode_<code>`, and common.ALARMS surfaces
# it verbatim -- useful for an automation, useless for a person: "E464" says
# nothing. The appliance's own app resolves it against a catalog of 45 codes
# (RACMOB_error_message_<code>_result in its language file, reached by taking the
# part after the underscore and lower-casing it, exactly as below).
#
# The English strings here are Samsung's own, quoted from that catalog rather
# than rewritten, so what Home Assistant shows is what the appliance's app would
# have shown. They stay in English regardless of the user's language: they are
# the message, not a label, and translating them here would mean inventing
# wording the appliance never used.
#
# A code outside this table becomes the code itself rather than nothing -- the
# table is what one app build knew, and an unrecognised code is still the thing a
# service call will ask for.
_ERROR_MESSAGES = {
    "E101": "Indoor unit communication reception error",
    "E121": "Short circuit or open circuit of the indoor temperature sensor",
    "E122": "A short or open circuit of the Eva-in sensor of the indoor unit",
    "E123": "A short or open circuit of the Eva-MID sensor of the indoor unit",
    "E154": "Indoor fan error",
    "E162": "EEPROM error",
    "E163": "EEPROM option setting error",
    "E201": "Indoor/outdoor unit communication error",
    "E202": "Indoor/outdoor unit communication error",
    "E203": "Main/Inv communication error",
    "E221": "Short circuit or open circuit of the outdoor temperature sensor",
    "E231": "Short circuit or open circuit of the cold temperature sensor",
    "E237": "Outdoor Cond. Out Sensor Short/Open",
    "E251": "Short circuit or open circuit of the output temperature sensor",
    "E320": "OLP sensor error",
    "E403": "Trip caused by indoor freeze",
    "E404": "Heating overload trip",
    "E416": "Output temperature trip",
    "E422": "Pipe blockage error",
    "E425": "Electric current error (INV)",
    "E440": "Heating stop at temperatures above the Start inhibit activation",
    "E441": "Cooling stop at temperatures above the Start inhibit activation",
    "E458": "Fan speed error (INV)",
    "E461": "Comp starting error (INV)",
    "E462": "Electric current trip",
    "E463": "OLP trip",
    "E464": "IPM Over Current (INV)",
    "E465": "Compressor overload protection (INV)",
    "E466": "DC-link voltage under/over error (INV)",
    "E467": "Comp rotation error (INV)",
    "E468": "Current sensor error (INV)",
    "E469": "DC-link voltage sensor error (INV)",
    "E470": "Outdoor unit EEPROM Read/Write Error",
    "E471": "INV/Outdoor transmission error EEPROM",
    "E472": "AC Line Zero Cross Signal out (INV)",
    "E473": "Comp Lock Error (INV)",
    "E474": "Heat sink errors (INV)",
    "E475": "FAN2 inverter error (INV)",
    "E483": "H/W DC Link Over detection (INV)",
    "E484": "PFC error (INV)",
    "E485": "Input voltage sensor error (INV)",
    "E488": "Input voltage sensor error (INV)",
    "E500": "Heat sink overheat error (INV)",
    "E554": "Gas shortage error",
    "E660": "Bootcode inverter error (INV)",
}


def _error_message(items):
    """The active error's message, its bare code if unknown, or 'none'.

    Reads the same rows common._active_alarm_codes does -- a row is live only
    when its state is not Deleted and its code does not carry the '_OFF'
    placeholder suffix -- and looks only at ErrorCode_ rows: FilterAlarm and
    friends are reminders with their own entities, not faults with a code.
    """
    if not items or not isinstance(items, list):
        return "none"
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("x.com.samsung.da.code") or "")
        if not code.upper().startswith("ERRORCODE_"):
            continue
        if str(item.get("x.com.samsung.da.state", "")).lower() == "deleted":
            continue
        value = code.split("_", 1)[1]
        if value.upper() in ("OFF", ""):
            continue
        return _ERROR_MESSAGES.get(value.upper(), value.upper())
    return "none"


ALARMS_WITH_MESSAGE = replace(
    common.ALARMS,
    entities=(
        *common.ALARMS.entities,
        SensorDesc(
            key="error_message",
            field="x.com.samsung.da.items",
            icon="mdi:alert-circle-outline",
            entity_category="diagnostic",
            value_fn=_error_message,
        ),
    ),
)


ENERGY_METER_LEGACY = replace(
    common.ENERGY_METER,
    match_fn=lambda rep, resources: is_legacy_board(resources),
    entities=tuple(
        replace(e, value_fn=_legacy_cumulative_power_kwh) if e.key == "energy_kwh" else e
        for e in common.ENERGY_METER.entities
    ),
)

# Non-legacy counterpart, needed so both caps can share this href without
# tripping the "multiple caps need a discriminator" build check.
ENERGY_METER_GENERIC = replace(
    common.ENERGY_METER,
    match_fn=lambda rep, resources: not is_legacy_board(resources),
)


def _has_option_token(prefix):
    return lambda rep, resources: (
        is_legacy_board(resources) and _option_token(rep, prefix) is not None
    )


def _option_token_on(prefix):
    return lambda rep: _option_token(rep, prefix) == "On"


def _option_token_num(prefix, offset=0, divisor=1):
    def read(rep):
        raw = _option_token(rep, prefix)
        try:
            return (float(raw) - offset) / divisor
        except (TypeError, ValueError):
            return None

    return read


def _option_switch_write(prefix):
    def write(payload, rep, href=None):
        return (["mode", "vs", "0"], {"x.com.samsung.da.options": option_write(prefix, payload)})

    return write


def _option_number_write(prefix):
    def write(payload, rep, href=None):
        return (
            ["mode", "vs", "0"],
            {"x.com.samsung.da.options": option_write(prefix, str(round(float(payload))))},
        )

    return write


def _odor_controller_active(rep):
    """Odor-controller self-clean on/off, from the `SmartCoolClean_<On/Off>`
    option token (matches the SmartThings cloud's airConditionerOdorController
    State field). Read-only: no confirmed write path."""
    tok = _option_token(rep, "SmartCoolClean")
    if tok is None:
        return None
    return tok == "On"


def _odor_controller_progress(rep):
    """0-100 progress of the odor-controller cycle, from the
    `ProgressSmartClean_<N>` token."""
    return _int(_option_token(rep, "ProgressSmartClean"))


def _humidity(rep):
    """Relative humidity, preferring the 5%-rounded field where present.

    ARTIK051 boards have no fivepercentHumidity and report plain `humidity`
    instead, which only populates for ~30s while "Air monitoring" is on
    before zeroing itself -- so 0 there means "not measuring" and is reported
    as unknown rather than 0%. fivepercentHumidity has no such quirk (issue
    #160), so its own 0 readings pass through unchanged.
    """
    if "x.com.samsung.da.fivepercentHumidity" in rep:
        return _num(rep["x.com.samsung.da.fivepercentHumidity"])
    if "x.com.samsung.da.humidity" in rep:
        value = _num(rep["x.com.samsung.da.humidity"])
        return value if value else None
    return None


def _climate_write(payload, rep, href=None):
    """Maps a (kind, value) command from the climate platform to the
    (path_segs, body) for that one sub-write; `value` is already the raw
    device code. Power always goes to vendor `/power/vs/0` (OCF `/power/0`
    is absent on most boards). Temperature channel (OCF vs vendor) is picked
    by the platform. Mode/fan/swing/preset are always the vendor `/x/vs/0`
    resources. Each write sends only its own field(s); the device merges the
    rest itself (see common.merge_items_field / merge_options_field)."""
    kind, value = payload
    if kind == "power":
        return (["power", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"})
    if kind == "mode":
        return (["mode", "vs", "0"], {"x.com.samsung.da.modes": [value]})
    if kind == "temperature_ocf":
        return (["temperature", "desired", "0"], {"temperature": round(float(value))})
    if kind == "temperature":
        # Vendor items[] array; only one item observed on every AC dump, id '0'.
        return (
            ["temperatures", "vs", "0"],
            {
                "x.com.samsung.da.items": [
                    {
                        "x.com.samsung.da.id": "0",
                        "x.com.samsung.da.desired": str(round(float(value))),
                    }
                ]
            },
        )
    if kind == "fan":
        return (["wind", "strength", "vs", "0"], {"x.com.samsung.da.modes": value})
    if kind == "swing":
        return (["wind", "direction", "vs", "0"], {"x.com.samsung.da.modes": value})
    if kind == "oscillation":
        # value is HA's swing_mode string; both axes are independent Swing|Fix
        # toggles written together (see climate.py's oscillation fallback).
        return (
            ["wind", "oscillation", "vs", "0"],
            {
                "vertical": "Swing" if value in ("vertical", "both") else "Fix",
                "horizontal": "Swing" if value in ("horizontal", "both") else "Fix",
            },
        )
    if kind == "fan_legacy":
        return (["airflow", "vs", "0"], {"x.com.samsung.da.speedLevel": str(value)})
    if kind == "swing_legacy":
        return (["airflow", "vs", "0"], {"x.com.samsung.da.direction": value})
    if kind == "preset_legacy":
        return (["mode", "vs", "0"], {"x.com.samsung.da.options": option_write("Comode", value)})
    if kind == "preset":
        return (["mode", "convenient", "vs", "0"], {"x.com.samsung.da.modes": value})
    return None


CLIMATE = Capability(
    href=HREF_MODE,
    poll_tier="warm",
    entities=(
        ClimateDesc(
            key="climate",
            translation_key="airconditioner",
            rep_fn=_first_mode,
            write_fn=_climate_write,
        ),
        # Panel light switch for boards that encode it in /mode/vs/0's options
        # instead of a dedicated /light/vs/0 (see _has_display_light_option).
        # Shares the switch.display_light translation key with DISPLAY_LIGHT
        # below; mutually exclusive per href.
        SwitchDesc(
            key="display_light",
            rep_fn=_display_light_on,
            exists_fn=_has_display_light_option,
            write_fn=_display_light_write,
            icon="mdi:led-on",
            entity_category="config",
        ),
        # Beep on/off from the Volume_* token. Applies uniformly across board
        # generations (issue #136: previously modeled as a graduated Number
        # for legacy boards, but no unit ever reported an intermediate value,
        # and the Number's write path couldn't produce the literal 'Mute'
        # token needed to turn it off).
        SwitchDesc(
            key="beep",
            rep_fn=_beep_on,
            exists_fn=lambda rep, resources: _option_token(rep, "Volume") is not None,
            write_fn=_beep_write,
            icon="mdi:volume-high",
            entity_category="config",
        ),
        # Tropical night level (Sleep_<N> token), gated off the legacy board
        # (its Sleep_ token is the good_sleep Number below instead). exists_fn
        # only proves the token slot is present, not that the feature is real
        # (issue #166 reports Sleep_0 on a unit confirmed to have no such
        # mode) -- disabled by default so units that do have it can enable it.
        NumberDesc(
            key="tropical_night_mode",
            rep_fn=_tropical_night_value,
            exists_fn=lambda rep, resources: (
                not is_legacy_board(resources) and _option_token(rep, "Sleep") is not None
            ),
            write_fn=_tropical_night_write,
            native_min=0,
            native_max=16,
            step=1,
            enabled_default=False,
            icon="mdi:weather-night",
            entity_category="config",
        ),
        # Settings that this board generation keeps as options[] tokens.
        SwitchDesc(
            key="spi",
            rep_fn=_option_token_on("Spi"),
            exists_fn=_has_option_token("Spi"),
            write_fn=_option_switch_write("Spi"),
            icon="mdi:air-purifier",
            entity_category="config",
        ),
        # Shares AUTO_CLEAN's catalog entry (same feature, different board
        # generation) under a distinct key.
        SwitchDesc(
            key="auto_clean_legacy",
            translation_key="auto_clean",
            rep_fn=_option_token_on("Autoclean"),
            exists_fn=_has_option_token("Autoclean"),
            write_fn=_option_switch_write("Autoclean"),
            icon="mdi:fan-auto",
            entity_category="config",
        ),
        # A drying cycle the unit runs after cooling, to keep the coil from going
        # mouldy. Three tokens describe it and the switch above only covered the
        # first: Autoclean_ is the setting, AutocleanProgress_ is how far a
        # running cycle has got, and StopAutoClean_ is a channel for ending one
        # early -- its presence is what says the appliance takes that at all
        # (the app gates its own stop button on exactly that), and the value it
        # reports while nothing is running is Idle.
        #
        # The percentage scale is the app's own: `<progress max="100">` with the
        # token rendered as "{{value}}%" beside it. An idle unit here reports 1
        # rather than 0, the same floor the laundry firmware's progressPercentage
        # sits at when Ready, so 0-vs-1 is not a reliable "is it running" test --
        # which is why the button below is not gated on it.
        #
        # The sensor shares AUTO_CLEAN's catalog entry, like auto_clean_legacy
        # above: same figure, different board generation. Distinct key, so
        # nothing collides if a board ever reported both.
        SensorDesc(
            key="auto_clean_progress_legacy",
            translation_key="auto_clean_progress",
            rep_fn=_option_token_num("AutocleanProgress"),
            exists_fn=_has_option_token("AutocleanProgress"),
            unit="%",
            state_class="measurement",
            icon="mdi:progress-check",
            entity_category="diagnostic",
        ),
        ButtonDesc(
            key="auto_clean_stop",
            field="",
            payload="StopAutoClean_Set",
            icon="mdi:fan-off",
            entity_category="config",
            exists_fn=_has_option_token("StopAutoClean"),
            write_fn=lambda p, rep, href=None: (
                ["mode", "vs", "0"],
                {"x.com.samsung.da.options": [p]},
            ),
        ),
        SwitchDesc(
            key="air_monitoring",
            rep_fn=_option_token_on("AirMonitoring"),
            exists_fn=_has_option_token("AirMonitoring"),
            write_fn=_option_switch_write("AirMonitoring"),
            icon="mdi:air-filter",
            entity_category="config",
        ),
        # "Good Sleep" timer. 0 = off; the upper bound is a guess (only 0 has
        # been observed on hardware), so a write above 0 is unverified.
        NumberDesc(
            key="good_sleep",
            rep_fn=_option_token_num("Sleep"),
            exists_fn=_has_option_token("Sleep"),
            write_fn=_option_number_write("Sleep"),
            native_min=0,
            native_max=12,
            step=1,
            unit="h",
            icon="mdi:sleep",
            entity_category="config",
        ),
        # Outdoor temperature, offset by 55 -- calibrated against an
        # independent thermometer (token 75 while it read 20.3°C).
        SensorDesc(
            key="outdoor_temperature",
            rep_fn=_option_token_num("OutdoorTemp", offset=55),
            exists_fn=_has_option_token("OutdoorTemp"),
            device_class="temperature",
            state_class="measurement",
            unit="°C",
            icon="mdi:home-thermometer-outline",
        ),
        # Filter time in tenths of an hour, counting UP since last filter
        # reset; scale and direction confirmed against the Samsung app and
        # the /alarms/vs/0 threshold crossing (500h). No reset entity: no
        # local write path has been found -- see
        # docs/investigations/ac-filter-reset.md for what's been tried.
        SensorDesc(
            key="filter_time",
            rep_fn=_option_token_num("FilterTime", divisor=10),
            exists_fn=_has_option_token("FilterTime"),
            device_class="duration",
            unit="h",
            state_class="measurement",
            icon="mdi:air-filter",
        ),
        # FilterTime_'s threshold, exposed as a static 4-way radio
        # (180/300/500/700h, matching the app) since options[] tokens carry
        # no supported-values list to read from, unlike air_filter_threshold
        # on newer boards.
        SelectDesc(
            key="filter_alarm_time",
            rep_fn=lambda rep: _option_token(rep, "FilterAlarmTime"),
            exists_fn=_has_option_token("FilterAlarmTime"),
            options=("180", "300", "500", "700"),
            write_fn=_option_switch_write("FilterAlarmTime"),
            icon="mdi:alarm",
            entity_category="config",
        ),
        # Odor-controller ("Smart Cool Clean") state + progress -- see
        # _odor_controller_active's docstring.
        BinarySensorDesc(
            key="odor_controller_active",
            rep_fn=_odor_controller_active,
            exists_fn=lambda rep, resources: _option_token(rep, "SmartCoolClean") is not None,
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="odor_controller_progress",
            rep_fn=_odor_controller_progress,
            exists_fn=lambda rep, resources: _option_token(rep, "ProgressSmartClean") is not None,
            unit="%",
            state_class="measurement",
            icon="mdi:progress-check",
            entity_category="diagnostic",
        ),
    ),
)

AIR_PURIFY = Capability(
    href="/option/airpurify/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="air_purify",
            field="x.com.samsung.da.modes",
            icon="mdi:air-purifier",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "airpurify", "vs", "0"],
                {"x.com.samsung.da.modes": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

AUTO_CLEAN = Capability(
    href="/option/autoclean/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="auto_clean",
            field="x.com.samsung.da.settingStatus",
            icon="mdi:spray-bottle",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "autoclean", "vs", "0"],
                {"x.com.samsung.da.settingStatus": "On" if p == "On" else "Off"},
            ),
        ),
        # Run state (vs settingStatus's "feature enabled"): status is
        # Start/Stop per the resource's own supportedStatus.
        BinarySensorDesc(
            key="auto_clean_running",
            field="x.com.samsung.da.status",
            icon="mdi:spray-bottle",
            entity_category="diagnostic",
            value_fn=lambda v: v == "Start",
        ),
        # Percent through the cycle; matches the appliance's own display.
        SensorDesc(
            key="auto_clean_progress",
            field="x.com.samsung.da.progress",
            icon="mdi:progress-clock",
            unit="%",
            state_class="measurement",
            entity_category="diagnostic",
            value_fn=common.int_or_none,
        ),
    ),
)

AIR_FILTER = Capability(
    href="/filter/airdustfilter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="air_filter_usage",
            rep_fn=filter_usage_percent,
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
        # Lifetime hour counter, resets only on filter replacement.
        SensorDesc(
            key="air_filter_usage_hours",
            field="x.com.samsung.da.filterUsage",
            device_class="duration",
            state_class="total_increasing",
            unit_fn=_filter_unit,
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=_int,
        ),
        # Locally writable alarm threshold (see _threshold_write); only
        # surfaces where supportedFilterDesiredUsage is advertised.
        SelectDesc(
            key="air_filter_threshold",
            field="x.com.samsung.da.filterDesiredUsage",
            options_field="x.com.samsung.da.supportedFilterDesiredUsage",
            exists_fn=lambda rep, res: bool(
                rep.get("x.com.samsung.da.supportedFilterDesiredUsage")
            ),
            icon="mdi:alarm",
            entity_category="config",
            write_fn=_threshold_write,
            value_fn=lambda v: str(v) if v is not None else None,
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


def _pm1_threshold_write(payload, rep, href=None):
    """Same contract as _threshold_write, against this filter's own href --
    not yet confirmed live, so the Select this backs stays gated behind
    supportedFilterDesiredUsage's presence, same as AIR_FILTER's."""
    return ["filter", "airdustPM1filter", "vs", "0"], {
        "x.com.samsung.da.filterDesiredUsage": payload,
    }


def _has_filter_field(field):
    return lambda rep, resources: rep.get(field) is not None


# Second, PM1-rated filter some TP1X_FAC boards report alongside AIR_FILTER's
# href (issue #270). Some units report only the capacity/unit fields with no
# live data at all, so every entity here is individually gated on its own
# field's presence.
AIR_FILTER_PM1 = Capability(
    href="/filter/airdustPM1filter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="air_filter_pm1_usage",
            rep_fn=filter_usage_percent,
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            exists_fn=_has_filter_field("x.com.samsung.da.filterUsage"),
        ),
        SensorDesc(
            key="air_filter_pm1_usage_hours",
            field="x.com.samsung.da.filterUsage",
            device_class="duration",
            state_class="total_increasing",
            unit_fn=_filter_unit,
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=_int,
            exists_fn=_has_filter_field("x.com.samsung.da.filterUsage"),
        ),
        SelectDesc(
            key="air_filter_pm1_threshold",
            field="x.com.samsung.da.filterDesiredUsage",
            options_field="x.com.samsung.da.supportedFilterDesiredUsage",
            exists_fn=lambda rep, res: bool(
                rep.get("x.com.samsung.da.supportedFilterDesiredUsage")
            ),
            icon="mdi:alarm",
            entity_category="config",
            write_fn=_pm1_threshold_write,
            value_fn=lambda v: str(v) if v is not None else None,
        ),
        SensorDesc(
            key="air_filter_pm1_status",
            field="x.com.samsung.da.filterStatus",
            device_class="enum",
            options=("normal", "wash", "replace"),
            translation_key="filter_status",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
            exists_fn=_has_filter_field("x.com.samsung.da.filterStatus"),
        ),
    ),
)

DISPLAY_LIGHT = Capability(
    href="/light/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="display_light",
            field="mode",
            icon="mdi:led-on",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["light", "vs", "0"],
                {"mode": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# UV-C sterilization LED (issue #270, TP1X_FAC_TIME_23K).
UV_LED = Capability(
    href="/uvled/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="uv_led",
            field="x.com.samsung.da.modes",
            icon="mdi:lightbulb-fluorescent-tube",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["uvled", "vs", "0"],
                {"x.com.samsung.da.modes": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Ventilation-reminder alarm toggle (issue #270). No supportedModes list to
# confirm the value set against, unlike UV_LED above -- not round-trip
# confirmed on real hardware.
VENTILATION_ALARM = Capability(
    href="/ventilation/setting/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="ventilation_alarm",
            field="alarm",
            icon="mdi:bell-alert",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["ventilation", "setting", "vs", "0"],
                {"alarm": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Confirmed against issue #38's dump (TP1X_DA-AC-RAC-01001_0000).
MUTE_ONCE = Capability(
    href="/option/muteonce/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="mute_once",
            field="muteonce",
            icon="mdi:volume-mute",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "muteonce", "vs", "0"],
                {"muteonce": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Circuit-breaker current-limit setting (issue #38, TP1X board). No unit/label
# in the dump to confirm what the levels mean -- exposed read-only per the
# 'don't guess' rule rather than risking an unverified write to live hardware.
CURRENT_LIMIT = Capability(
    href="/electriccurrent/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="current_limit_enabled",
            field="operation",
            icon="mdi:current-ac",
            entity_category="diagnostic",
            value_fn=lambda v: v == "On",
        ),
        SensorDesc(
            key="current_limit_level",
            field="modes",
            icon="mdi:current-ac",
            entity_category="diagnostic",
        ),
    ),
)

# Overload-response setting (issue #126, TP1X_DA-AC-RAC-01011 WindFree). No
# confirmation of the behavioral difference between modes -- read-only, same
# precedent as CURRENT_LIMIT above.
ANOMALY_LOAD = Capability(
    href="/anomalyload/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="overload_protection_active",
            field="operation",
            icon="mdi:flash-alert",
            entity_category="diagnostic",
            value_fn=lambda v: v == "On",
        ),
        SensorDesc(
            key="overload_protection_mode",
            field="mode",
            device_class="enum",
            options=("alarm", "powersaving"),
            translation_key="overload_protection_mode",
            icon="mdi:flash-alert",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
    ),
)

# Absence-detection power-saving (issue #173, TP1X_LNX-AC-RAC-01001). `status`
# is a bare On/Off with the same shape already shipped writable elsewhere in
# this file, so it's a switch despite no live-confirmed write. `mode` stays
# read-only: no dump evidence for what writing it does to a running
# compressor.
ABSENCE_POWER_SAVING = Capability(
    href="/mds/absencepowersaving/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="absence_power_saving_active",
            field="status",
            icon="mdi:human-greeting-proximity",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["mds", "absencepowersaving", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        SensorDesc(
            key="absence_power_saving_mode",
            field="switchPowerSaveMode",
            device_class="enum",
            options=("eco", "normal", "comfort"),
            translation_key="absence_power_saving_mode",
            icon="mdi:leaf",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
    ),
)

# Avoid-direct-wind-on-motion, a sibling AI feature to ABSENCE_POWER_SAVING on
# the same dump; same shape and reasoning.
MOTION_DETECT_WIND = Capability(
    href="/option/motiondetectwind/stateful/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="motion_detect_wind_active",
            field="status",
            icon="mdi:motion-sensor",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "motiondetectwind", "stateful", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        SensorDesc(
            key="motion_detect_wind_mode",
            field="modes",
            device_class="enum",
            options=("direct", "indirect"),
            translation_key="motion_detect_wind_mode",
            icon="mdi:weather-windy",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
    ),
)

# Standalone temperature sensor for history/automations (issue #75); the
# climate card only exposes current_temperature as an attribute. Shares key
# 'current_temperature_c' with the _VS variant below so only one ever binds.
CURRENT_TEMPERATURE = Capability(
    href=HREF_TEMP_CURRENT,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="current_temperature_c",
            field="temperature",
            device_class="temperature",
            state_class="measurement",
            unit_fn=lambda rep: normalize_temp_unit(rep.get("units"), "°C"),
        ),
    ),
)

CURRENT_TEMPERATURE_VS = Capability(
    href=HREF_TEMPS_VS,
    poll_tier="warm",
    match_fn=lambda rep, resources: HREF_TEMP_CURRENT not in resources,
    entities=(
        SensorDesc(
            key="current_temperature_c",
            rep_fn=_temps_vs_current,
            device_class="temperature",
            state_class="measurement",
            unit_fn=_temps_vs_unit,
        ),
    ),
)

# fivepercentHumidity is the only live reading on most dumps; the OCF
# /humidity/0 resource and this vendor resource's own `humidity` field both
# read a stuck 0 where fivepercentHumidity is absent. See _humidity's
# docstring for the ARTIK051 fallback and its zero-as-"not measuring" quirk.
HUMIDITY = Capability(
    href="/humidity/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="humidity",
            rep_fn=_humidity,
            device_class="humidity",
            state_class="measurement",
            unit="%",
        ),
    ),
)

# /sensors/vs/0 items[] carry live air-quality readings. CleanLevel is
# corroborated as numeric by a top-level x.com.samsung.da.cleanLevel scalar,
# so it's a measurement; the others stay string diagnostics (see
# _sensor_item_value). All disabled by default: _has_sensor_type only proves
# the item type is listed, not that the sensor is real (see its docstring).
AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="clean_level",
            field="x.com.samsung.da.items",
            icon="mdi:broom",
            entity_category="diagnostic",
            state_class="measurement",
            exists_fn=_has_sensor_type("CleanLevel"),
            enabled_default=False,
            value_fn=lambda items: _int(_sensor_item_value(items, "CleanLevel")),
        ),
        *tuple(
            SensorDesc(
                key=key,
                field="x.com.samsung.da.items",
                icon=icon,
                entity_category="diagnostic",
                exists_fn=_has_sensor_type(type_),
                enabled_default=False,
                value_fn=lambda items, t=type_: _sensor_item_value(items, t),
            )
            for key, icon, type_ in (
                ("odor", "mdi:weather-windy", "Odor"),
                ("dust", "mdi:cloud", "Dust"),
                ("fine_dust", "mdi:cloud-outline", "FineDust"),
                ("super_fine_dust", "mdi:weather-fog", "SuperFineDust"),
            )
        ),
    ),
)


# AC-scoped coverage: CLIMATE_CONSUMED_HREFS (read by the climate entity)
# plus vendor-duplicate / ambiguous / plumbing resources. These stay out of
# the global ignored.IGNORED because several collide with other families'
# schemas. A no-entity Capability still marks the href bound so discover()
# reports no gap. CLIMATE_CONSUMED_HREFS are pinned to 'warm' (rather than the
# Capability default of 'cold') so their state changes push instead of
# waiting on the ~30s full-summary sweep (issue #17).
_AC_IGNORED = [
    "/humidity/0",  # OCF mirror, stuck at 0 on every dump seen
    "/personality/presence/vs/0",  # presence-personalization plumbing (empty)
    "/airflow/0",  # OCF mirror of /airflow/vs/0; vendor form is the one used
    # TP1X/TP2X-class housekeeping / opaque blobs with no user-actionable
    # state or documented write contract. /option/muteonce/vs/0 and
    # /selfcheck/vs/0 are deliberately NOT here -- see MUTE_ONCE above and
    # common.SELF_CHECK, both of which have a confirmed, modelable contract.
    "/airlevelcheck/vs/0",  # periodic air-quality sensing scheduler plumbing
    "/aisleep/vs/0",  # AI-sleep feedback state (no actionable control)
    "/availablecontrolsets/vs/0",  # opaque hex-encoded control-set bitmap
    "/da/softreset/vs/0",  # soft-reset trigger plumbing
    "/keepnormalstate/vs/0",  # internal keep-normal flag
    "/mds/absencemonitoring/vs/0",  # motion-detection sensor plumbing (empty)
    "/mds/absencestate/vs/0",  # motion-detection state (empty here)
    "/remotedatacontrol/vs/0",  # remote data-control session status
    "/remotedeviceinfo/vs/0",  # remote paired-device id list (empty here)
    "/remotetemperature/vs/0",  # external temp-sensor feed (unset here)
    # Manual airflow-step position; overlaps the swing control already on the
    # climate card, and the numeric-step meaning isn't documented.
    "/stepcontrol/vs/0",
    "/reserverulesets/vs/0",  # opaque hex-encoded schedule reservation blob
    "/welcome/temperature/vs/0",  # welcome-cooling plumbing
    # System-AC-only (multi-indoor-subdevice commercial installs, issue #52):
    # opaque hex-encoded installation topology, not user-actionable state.
    "/sac/installationinfo/vs/0",
    # Wind-Free 2-in-1 systems (issues #150/#153): paired-subdevice id list.
    # registry/subdevices.py reads this same field to reach the second
    # indoor subdevice when it's populated -- see that module's Pattern B.
    "/subdevices/vs/0",
    "/runn/vs/0",  # undocumented single int (runningMode: always 0 seen)
    # 2-in-1/multi-indoor-subdevice systems (issue #177): confirmed read-only
    # subdevice count. Fetched separately by
    # registry.subdevices.enumerate_subdevices, hence the entry here rather
    # than a coverage gap.
    "/multidevice/vs/0",
]

# Built as bare no-entity caps; folded into the AC registry (not global).
# HREF_TEMP_CURRENT and HREF_TEMPS_VS are excluded -- CURRENT_TEMPERATURE /
# CURRENT_TEMPERATURE_VS above already cover those with real entities.
COVERAGE = [
    Capability(href=h, poll_tier="warm")
    for h in CLIMATE_CONSUMED_HREFS
    if h not in (HREF_TEMP_CURRENT, HREF_TEMPS_VS)
] + [Capability(href=h) for h in _AC_IGNORED]

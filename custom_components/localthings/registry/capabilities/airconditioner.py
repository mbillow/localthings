"""Capabilities for the Samsung air-conditioner family (ARTIK051_PRAC-class,
issue #17 / ARTIK051_PRAC_20K).

Core controls (power, mode, temperature, fan, swing, preset) surface as one
composite HA `climate` entity; climate.py reads the sibling resources bound
here off the coordinator snapshot. These caps stay out of the global
`ALL`/`CAPABILITIES`: several hrefs (`/mode/vs/0`, `/temperatures/vs/0`,
`/humidity/*`) collide with other families' schemas (see
capabilities/__init__.py) -- AC-only, by_type registry only.
"""

import math
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
from .common import (
    filter_reset_button,
    filter_usage_hours,
    filter_usage_percent,
    has_sensor_type,
    normalize_temp_unit,
)
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
    array; only v[0] is used. v[1] is the device's own graded air-quality
    level for that reading, left unbound because its floor differs by
    family -- see common.sensor_item_value for the full note.

    No device_class here: unlike air_purifier (issue #325), no AC family
    has had its dust readings correlated against the app, and every AC
    fixture reports permanent zeros or ties, so this file's own dumps
    supply no grade-band evidence either. Returns a string rather than an
    int, which these diagnostic entities have always done."""
    for it in items or []:
        if isinstance(it, dict) and it.get("x.com.samsung.da.type") == type_:
            v = it.get("x.com.samsung.da.value")
            if isinstance(v, list) and v:
                return str(v[0])
            return None
    return None


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


# Samsung's "System Fresh Air Ventilator" (PR #316, model
# ACA-KR-TP2-21-AN9000, vid DA-AC-DIFFUSER-01001) self-reports oic.d.
# airconditioner and routes through this same CLIMATE capability, but its
# /mode/vs/0 supportedModes are Purification/Ventilation/SmartVentilation --
# none of which climate.py's HVAC-mode table knows, so hvac_mode collapses
# to a single stuck value with no way to tell the three apart. Gated to
# devices whose *entire* supported-mode set is this vocabulary, so it can't
# false-positive on a real AC's Cool/Heat/Dry list.
_VENTILATION_MODE_VALUES = frozenset(("Purification", "Ventilation", "SmartVentilation"))


def _is_ventilation_mode_device(rep, resources):
    supported = rep.get("x.com.samsung.da.supportedModes")
    if not isinstance(supported, (list, tuple)) or not supported:
        return False
    return set(supported) <= _VENTILATION_MODE_VALUES


def _ventilation_mode_write(payload, rep, href=None):
    return ["mode", "vs", "0"], {"x.com.samsung.da.modes": [payload]}


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


def option_bit(rep, prefix, index, width):
    """One capability bit out of the `OptionCode_<n>` / `ExtendOptionCode_<n>` token.

    The appliance packs which features it has into two integers. Its own app reads
    them by turning the number into binary, left-padding to a fixed width, and
    indexing that *string* -- so index 0 is the most significant bit, and the
    indices below are the app's own (`racOptionCodeValue[12]`, and so on), kept
    identical so the two can be compared line by line.

    Returns None when the token is absent, which is not the same as a zero: a bit
    that is present and 0 says the feature is missing, while no token at all says
    only that this board does not publish the map.
    """
    raw = _option_token(rep, prefix)
    if raw is None:
        return None
    try:
        bits = format(int(raw), f"0{width}b")
    except (TypeError, ValueError):
        return None
    if len(bits) != width or not 0 <= index < width:
        return None  # a number too wide for the map is not one we can read
    return bits[index] == "1"


def option_code_bit(rep, index):
    """A bit of the 16-wide `OptionCode` map."""
    return option_bit(rep, "OptionCode", index, 16)


def extend_option_code_bit(rep, index):
    """A bit of the 32-wide `ExtendOptionCode` map."""
    return option_bit(rep, "ExtendOptionCode", index, 32)


def has_option_code(rep):
    """Whether this board publishes the 16-wide capability map at all."""
    return _option_token(rep, "OptionCode") is not None


def has_extend_option_code(rep):
    """Whether this board publishes the 32-wide capability map at all.

    Its own name in the app is "Single RAC new option code, as old option code
    is full", and every RAC-class dump on record carries it while the FAC/CAC
    ones carry only the older map with values small enough that RAC bit
    positions read as zeros. So its presence is the closest thing available to
    "this is the family those bit positions were documented for" -- a proxy,
    not a proof, and used only to decide whether to read the map at all.
    """
    return _option_token(rep, "ExtendOptionCode") is not None


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
    # 0 reads as unknown, same as common.lifetime_wh_to_kwh.
    return round(n / 100000.0, 2) if n else None


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


def _has_option_token_any_board(prefix):
    """Token-presence test with no board-generation gate.

    `_has_option_token` above requires `is_legacy_board`, which was right for
    the settings it guards but wrong for a token whose presence is itself the
    only signal that needs checking -- issue #367 found `OutdoorTemp_` live
    on 14 of 23 recorded fixtures despite none of them being legacy boards.
    Same shape as `beep`'s Volume_ test and `_has_display_light_option`,
    which already treat token presence as sufficient across generations."""
    return lambda rep, resources: _option_token(rep, prefix) is not None


def _reports_celsius(resources):
    """Whether this subdevice's own /temperatures/vs/0 declares Celsius (or
    says nothing at all, `_temps_vs_unit`'s default).

    Guards `OutdoorTemp_`'s -55 offset below: issue #367's field validation
    (48h against weather.forecast_home, r=0.92) ran on Celsius-locale boards
    only -- 13 of the 14 non-legacy fixtures that carry the token declare
    Celsius, and the offset's own calibration comment is a Celsius reading
    too. The one Fahrenheit-locale exception on record
    (`airconditioner_lnx_rac_heatpump`) has nothing to confirm the same
    additive constant, or degrees C rather than F, still hold -- so this
    stays off boards that declare anything else, rather than guess."""
    return _temps_vs_unit(resources.get(HREF_TEMPS_VS) or {}) == "°C"


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


def _option_number_write(prefix, factor=1):
    """Write a numeric options token. `factor` converts the entity's unit into the
    token's own: good_sleep is offered in hours while the token counts half hours."""

    def write(payload, rep, href=None):
        return (
            ["mode", "vs", "0"],
            {"x.com.samsung.da.options": option_write(prefix, str(round(float(payload) * factor)))},
        )

    return write


def _good_sleep_write(payload, rep, href=None):
    """Good Sleep needs its mode token in the same write as its duration.

    `Sleep_<n>` on its own is answered 2.04 Changed and then thrown away:
    measured on an ARTIK051_KRAC_18K, writing `["Sleep_4"]` left the token at
    `Sleep_0` at both +8s and +45s, while the same value written together with
    `Comode_Sleep` held. So the number is a parameter of the mode, not a
    setting of its own, and the appliance's app never sends one without the
    other either.

    Which mode token goes with it depends on nano wind, the way the app decides
    it: nano and Good Sleep share the single `Comode_` slot, so running both is
    `Comode_NanoSleep`, and switching the timer off while nano is on leaves nano
    running rather than turning everything off.
    """
    half_hours = round(float(payload) * 2)
    nano = _option_token(rep, "Comode") in ("Nano", "NanoSleep")
    if half_hours:
        comode = "Comode_NanoSleep" if nano else "Comode_Sleep"
    else:
        comode = "Comode_Nano" if nano else "Comode_Off"
    return (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": [comode, f"Sleep_{half_hours}"]},
    )


# What the appliance itself picks when a Good Sleep mode is asked for with no
# duration to go with it: writing a bare `Comode_Nano` over a live
# `Comode_Sleep`/`Sleep_4` came back as `Comode_NanoSleep`/`Sleep_16`. Used only
# when a sleep preset is selected while the timer reads 0.
_DEFAULT_SLEEP_HALF_HOURS = 16


def _preset_options(code, rep):
    """The options array for a legacy preset write.

    One `Comode_` token has to express both nano wind and Good Sleep, so
    selecting nano while the timer is running means `Comode_NanoSleep` -- and it
    has to carry the duration, because the board otherwise supplies its own.
    Measured: `["Comode_Nano"]` written over `Comode_Sleep`/`Sleep_4` came back
    as `Comode_NanoSleep`/`Sleep_16`, silently turning the user's two hours into
    eight. Writing the pair keeps the two hours.

    Leaving a sleep mode needs no such care: the board zeroes the duration by
    itself. Measured on the same unit -- a bare `["Comode_Off"]` written over
    `Comode_Sleep`/`Sleep_4` read back as `Comode_Off`/`Sleep_0` at +8s and +38s
    -- so the `none` preset cannot leave a stale token behind for the next nano
    selection to pick up as a running timer.
    """
    sleep = _option_token(rep, "Sleep")
    running = sleep not in (None, "0")
    if code == "Nano" and running:
        code = "NanoSleep"
    if code in ("Sleep", "NanoSleep"):
        return [f"Comode_{code}", f"Sleep_{sleep if running else _DEFAULT_SLEEP_HALF_HOURS}"]
    return option_write("Comode", code)


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


def _quantize_temperature(value, step):
    """Quantize a temperature to the device's advertised step size.

    Mirrors climate.py's `target_temperature_step`, which defaults to `1.0`
    when no increment is advertised anywhere (`step=None` or `<=0` here) --
    so a board with no increment still gets whole-degree writes instead of
    the raw value passed through unrounded. Returns `None` for a payload
    that isn't numeric, so the caller can reject the write instead of
    building a body around a null (coordinator.py's async_send_command
    already logs and drops a write_fn result of None).

    Normalizes an integral result to `int` here, once, so both write
    branches serialize `24` rather than `24.0` -- `round(..., 2)` guards
    against float noise in the division (e.g. `21.7 / 0.1`).
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        # float('nan')/float('inf') pass the try/except above (they're
        # valid floats) but round()/division on them raises ValueError/
        # OverflowError instead of returning -- reject here so a bad
        # payload always comes back as a clean None, not an exception
        # out of write_fn.
        return None
    step_value = _num(step) or 1.0
    if step_value <= 0:
        step_value = 1.0
    quantized = round(round(numeric / step_value) * step_value, 2)
    return int(quantized) if quantized.is_integer() else quantized


def _temperature_step(resources):
    """Device-reported temperature increment, read the same way and in the
    same order as climate.py's `target_temperature_step`: OCF
    `/temperature/control/vs/0`'s `increment` (or its vendor-prefixed
    twin), falling back to vendor `/temperatures/vs/0`. The increment on
    that second resource lives inside its `items[]` array like every other
    per-item field, not at the resource's top level -- unwrapped via
    `_temps_vs_item()`, the same helper `_temps_vs_current`/`_temps_vs_unit`
    use above, rather than read off `resource` directly.

    `rep` (the bound entity's own resource, `/mode/vs/0` for ClimateDesc --
    see rep_fn=_first_mode below) never carries an increment, so it isn't a
    candidate here; only the coordinator's `resources` snapshot is."""
    if not isinstance(resources, dict):
        return None
    control = resources.get(HREF_TEMP_CONTROL)
    if isinstance(control, dict):
        step = _num(control.get("increment")) or _num(control.get("x.com.samsung.da.increment"))
        if step is not None:
            return step
    temps_vs = resources.get(HREF_TEMPS_VS)
    if isinstance(temps_vs, dict):
        step = _num(_temps_vs_item(temps_vs).get("x.com.samsung.da.increment"))
        if step is not None:
            return step
    return None


def _climate_write(payload, rep, href=None, resources=None):
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
    if kind in ("temperature_ocf", "temperature"):
        quantized = _quantize_temperature(value, _temperature_step(resources))
        if quantized is None:
            return None
        if kind == "temperature_ocf":
            return (["temperature", "desired", "0"], {"temperature": quantized})
        # Vendor items[] array; only one item observed on every AC dump, id '0'.
        return (
            ["temperatures", "vs", "0"],
            {
                "x.com.samsung.da.items": [
                    {
                        "x.com.samsung.da.id": "0",
                        "x.com.samsung.da.desired": str(quantized),
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
        return (["mode", "vs", "0"], {"x.com.samsung.da.options": _preset_options(value, rep)})
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
        # Purification/Ventilation/SmartVentilation mode select (PR #316) --
        # _is_ventilation_mode_device gates this to devices using that
        # vocabulary exclusively, so a real AC's climate card is unaffected.
        SelectDesc(
            key="ventilation_mode",
            rep_fn=_first_mode,
            exists_fn=_is_ventilation_mode_device,
            options_field="x.com.samsung.da.supportedModes",
            icon="mdi:air-filter",
            write_fn=_ventilation_mode_write,
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
        # "Good Sleep" timer, offered in hours. 0 = off.
        #
        # The token counts *half* hours, so it is halved on the way in and doubled
        # on the way out. The appliance's own app pairs a picker of durations with
        # the values it puts on the wire, one to one:
        #
        #   0:00 0:30 1:00 1:30 2:00 2:30 3:00 4:00 5:00 ... 12:00
        #      0    1    2    3    4    5    6    8   10  ...    24
        #
        # which also pins the maximum at 12 hours (its own help text says so:
        # "will be turned off after a selected period of time (Max. 12 hours)").
        # Before this the token was published as if it were hours: the entity
        # capped at 12, which set six, and twelve hours could not be asked for at
        # all.
        #
        # Half-hour steps are what the app offers below three hours; above that it
        # offers whole hours only, and a half hour up there is untested rather than
        # known-bad. A Number cannot change step part-way, and turning this into a
        # Select of the app's sixteen values would change the entity's domain on
        # every unit that already has it, so the step stays 0.5 throughout.
        NumberDesc(
            key="good_sleep",
            rep_fn=_option_token_num("Sleep", divisor=2),
            exists_fn=_has_option_token("Sleep"),
            write_fn=_good_sleep_write,
            native_min=0,
            native_max=12,
            step=0.5,
            unit="h",
            icon="mdi:sleep",
            entity_category="config",
        ),
        # Outdoor temperature, offset by 55 -- calibrated against an
        # independent thermometer (token 75 while it read 20.3°C).
        #
        # exists_fn is token-presence-only (issue #367), not is_legacy_board:
        # gating it there dropped the sensor on every non-legacy board that
        # reports it -- 14 of 17 fixtures carrying the token in the issue's
        # own survey. A 48h/289-sample field capture correlated it against
        # weather.forecast_home at r=0.92, ruling out a firmware constant.
        # `_reports_celsius` keeps that presence check from also claiming
        # the -55 Celsius offset for board generations it was never
        # validated on -- see its own docstring.
        #
        # enabled_default=False: multi-split installs (multiple indoor heads
        # on one outdoor condenser) report the same token on every head, so
        # a fix here creates one identical sensor per head rather than one
        # per physical unit (same duplication as the shared energy/power
        # counters in issue #329). Left disabled so a user with several
        # heads can enable just one instead of getting N duplicates active
        # by default.
        SensorDesc(
            key="outdoor_temperature",
            rep_fn=_option_token_num("OutdoorTemp", offset=55),
            exists_fn=lambda rep, resources: (
                _has_option_token_any_board("OutdoorTemp")(rep, resources)
                and _reports_celsius(resources)
            ),
            device_class="temperature",
            state_class="measurement",
            unit="°C",
            enabled_default=False,
            icon="mdi:home-thermometer-outline",
        ),
        # Filter time in tenths of an hour, counting UP since last filter
        # reset; scale and direction confirmed against the Samsung app and
        # the /alarms/vs/0 threshold crossing (500h). Resettable via the
        # FilterCleanAlarm_Clear trigger token below -- see
        # docs/investigations/ac-filter-reset.md for how that was found.
        SensorDesc(
            key="filter_time",
            rep_fn=_option_token_num("FilterTime", divisor=10),
            exists_fn=_has_option_token("FilterTime"),
            device_class="duration",
            unit="h",
            state_class="measurement",
            icon="mdi:air-filter",
        ),
        # Resets the counter above via the FilterCleanAlarm_Clear token, the
        # same single-token options merge as every other /mode/vs/0 setting.
        # It's a trigger, not a stored setting -- the board zeroes the
        # counter on receipt and never reports this token back, which is why
        # it doesn't show up in exists_fn like the others. Confirmed on an
        # ARTIK051_KRAC_18K: FilterTime_95 -> FilterTime_0, stable across a
        # fresh DTLS session and every poll after (see the investigation doc
        # for why this was believed cloud-only until now).
        ButtonDesc(
            key="filter_time_reset",
            field="",
            payload="FilterCleanAlarm_Clear",
            icon="mdi:restart",
            entity_category="config",
            exists_fn=_has_option_token("FilterTime"),
            write_fn=lambda p, rep, href=None: (
                ["mode", "vs", "0"],
                {"x.com.samsung.da.options": [p]},
            ),
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
        # Lifetime hour counter, resets only on filter replacement. Derived
        # from the percentage and filterCapacity (issue #330) -- filterUsage
        # itself is not an hour count.
        SensorDesc(
            key="air_filter_usage_hours",
            rep_fn=filter_usage_hours,
            device_class="duration",
            state_class="total_increasing",
            unit_fn=_filter_unit,
            icon="mdi:air-filter",
            entity_category="diagnostic",
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
        filter_reset_button("air_filter_reset", "/filter/airdustfilter/vs/0"),
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
            rep_fn=filter_usage_hours,
            device_class="duration",
            state_class="total_increasing",
            unit_fn=_filter_unit,
            icon="mdi:air-filter",
            entity_category="diagnostic",
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
        filter_reset_button("air_filter_pm1_reset", "/filter/airdustPM1filter/vs/0"),
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

# TP1X_DA-AC-FAC-class additions (issue #319): most of these hrefs are the
# same shapes air_purifier.py already models on the sibling TP1X_DA-AC-AIR
# board (DISPLAY/SOUND_OUTPUT/SOUND_VOLUME, reused directly in the
# registry); SOUND_MODE and the two below are genuinely new.

SOUND_MODE = Capability(
    href="/settings/sound/mode/vs/0",
    poll_tier="cold",
    entities=(
        # Values seen (mute/tone/voice) are exactly laundry.SOUND_MODE's
        # vocabulary, so this shares that catalog entry -- but reads the
        # live supportedModes field rather than laundry's static tuple,
        # since this resource carries one (issue #319).
        #
        # exists_fn is required, not optional here: this board's rep never
        # reports a live 'mode' value ({"supportedModes": [...]} only), and
        # entity.py's default field-presence gate would otherwise keep the
        # select from ever registering -- adapter.flatten() (what the
        # golden/tests read) has no such gate, so it would look bound while
        # silently absent from HA. Register on supportedModes' presence
        # instead; current_option reads unknown until the device reports
        # 'mode' live.
        SelectDesc(
            key="sound_mode",
            field="mode",
            icon="mdi:volume-high",
            entity_category="config",
            options_field="supportedModes",
            exists_fn=lambda rep, resources: bool(rep.get("supportedModes")),
            write_fn=lambda p, rep, href=None: (
                ["settings", "sound", "mode", "vs", "0"],
                {"mode": p},
            ),
        ),
    ),
)

# Absence-detection auto air clean (issue #319) -- a plain On/Off toggle,
# sibling feature to ABSENCE_POWER_SAVING above but on its own href.
ABSENCE_CLEAN = Capability(
    href="/csi/absenceclean/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="absence_clean",
            field="mode",
            icon="mdi:broom",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["csi", "absenceclean", "vs", "0"],
                {"mode": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# The CAC-class board (issue #191) reports the identical {mode,
# supportedModes: [On, Off]} shape under /mds/absenceclean/vs/0 instead --
# confirmed against that board's own fixture, not guessed. Shares
# ABSENCE_CLEAN's key/translation: no dump has ever reported both hrefs
# together, so there's nothing for the two to collide over in
# adapter.flatten().
MDS_ABSENCE_CLEAN = Capability(
    href="/mds/absenceclean/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="absence_clean",
            field="mode",
            icon="mdi:broom",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["mds", "absenceclean", "vs", "0"],
                {"mode": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Energy-saving schedule (issue #319). `mode` is a device-chosen preset
# (e.g. Cooling_60/Off_180) with no confirmed unit for the trailing number
# (minutes seen elsewhere on this board are unprefixed ints, not
# underscore-suffixed) -- exposed as a select over the live options rather
# than translating labels we can't confirm. `state`/`operatingStatus` stay
# bare diagnostic passthroughs for the same reason.
ENERGY_SAVING = Capability(
    href="/csi/energysaving/vs/0",
    poll_tier="cold",
    entities=(
        SelectDesc(
            key="energy_saving_mode",
            field="mode",
            icon="mdi:leaf",
            entity_category="config",
            options_field="supportedModes",
            write_fn=lambda p, rep, href=None: (
                ["csi", "energysaving", "vs", "0"],
                {"mode": p},
            ),
        ),
        SensorDesc(
            key="energy_saving_state",
            field="state",
            icon="mdi:leaf",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="energy_saving_operating_status",
            field="operatingStatus",
            icon="mdi:leaf",
            entity_category="diagnostic",
        ),
    ),
)

# TP1X_DA-AC-CAC-01001-class additions (issue #288, six System A/C cassette
# units on the same board test_airconditioner_cac.py's coverage-gap test
# documents). `convenientMode`/`operatingOption` stay unexposed -- present
# on every dump seen but no evidence of what either actually controls.
EDGE_LIGHTING = Capability(
    href="/edgelighting/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="edge_lighting",
            field="status",
            icon="mdi:led-strip",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["edgelighting", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="edge_lighting_mode",
            field="mode",
            icon="mdi:led-strip-variant",
            entity_category="config",
            options_field="modeSupportedList",
            write_fn=lambda p, rep, href=None: (
                ["edgelighting", "vs", "0"],
                {"mode": p},
            ),
        ),
        # Color temperature in Kelvin (3000K/4000K/6500K), not a hue -- a
        # select over the live-reported codes rather than a light color_temp
        # entity, consistent with this project's other Kelvin-coded selects.
        SelectDesc(
            key="edge_lighting_color",
            field="colorOption",
            icon="mdi:palette",
            entity_category="config",
            options_field="colorSupportedList",
            write_fn=lambda p, rep, href=None: (
                ["edgelighting", "vs", "0"],
                {"colorOption": p},
            ),
        ),
    ),
)

# Second, distinct light resource on this board generation -- an
# always-on-style indicator light with its own status/mode, not to be
# confused with EDGE_LIGHTING (a different href/rep entirely) or
# DISPLAY_LIGHT (/light/vs/0's ambient mood light).
LIGHT_STATEFUL = Capability(
    href="/light/stateful/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="indicator_light",
            field="status",
            icon="mdi:led-on",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["light", "stateful", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="indicator_light_mode",
            field="mode",
            icon="mdi:led-variant-on",
            entity_category="config",
            options_field="supportedModes",
            write_fn=lambda p, rep, href=None: (
                ["light", "stateful", "vs", "0"],
                {"mode": p},
            ),
        ),
    ),
)

# Wind-Free / Wind-Sleep mode toggles (PR #316, ACA-KR-TP2-21-AN9000). Each
# on its own dedicated href, so unlike ventilation_mode above these need no
# device gating -- absent on every other family's dump. Write contract
# extrapolated from this file's other plain On/Off options-array fields
# (AIR_PURIFY, AUTO_CLEAN); not confirmed live.
#
# NOT the same WindFree already modeled elsewhere: on regular AC boards,
# WindFree is a `Comode_Nano` token inside /mode/vs/0's options[], surfaced
# as a climate preset (climate.py's _LEGACY_PRESET_CODES/preset_mode) with
# real coupling to hvac_mode (disabled in Heat/AIComfort/Auto, timing rules
# on legacy boards). This device's windfree/windsleep are bare booleans on
# their own hrefs with no such coupling evidenced -- same feature name,
# different wire mechanism, so plain switches rather than folding into
# climate.py's preset machinery.
WINDFREE = Capability(
    href="/modeoption/windfree/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="windfree",
            field="x.com.samsung.da.windfree",
            icon="mdi:leaf",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["modeoption", "windfree", "vs", "0"],
                {"x.com.samsung.da.windfree": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

WINDSLEEP = Capability(
    href="/modeoption/windsleep/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="windsleep",
            field="x.com.samsung.da.windsleep",
            icon="mdi:sleep",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["modeoption", "windsleep", "vs", "0"],
                {"x.com.samsung.da.windsleep": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# /sensors/vs/0 items[] carry live air-quality readings. CleanLevel is
# corroborated as numeric by a top-level x.com.samsung.da.cleanLevel scalar,
# so it's a measurement; the others stay string diagnostics (see
# _sensor_item_value). All disabled by default: has_sensor_type only proves
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
            exists_fn=has_sensor_type("CleanLevel"),
            enabled_default=False,
            value_fn=lambda items: _int(_sensor_item_value(items, "CleanLevel")),
        ),
        *tuple(
            SensorDesc(
                key=key,
                field="x.com.samsung.da.items",
                icon=icon,
                entity_category="diagnostic",
                exists_fn=has_sensor_type(type_),
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
        # CO2 (PR #316, ACA-KR-TP2-21-AN9000) -- a type this file's other AC
        # families don't report. Same field/shape air_monitor.SENSORS
        # already models with device_class='carbon_dioxide'/unit='ppm', so
        # this matches that descriptor rather than guessing fresh. The dust
        # keys above stay untyped for the reason in _sensor_item_value: the
        # pm10/pm25/pm1 mapping is confirmed for the purifier and monitor
        # families (issue #325), but no AC family has evidence of its own.
        SensorDesc(
            key="co2",
            field="x.com.samsung.da.items",
            icon="mdi:molecule-co2",
            entity_category="diagnostic",
            device_class="carbon_dioxide",
            state_class="measurement",
            unit="ppm",
            exists_fn=has_sensor_type("CO2"),
            enabled_default=False,
            value_fn=lambda items: _int(_sensor_item_value(items, "CO2")),
        ),
    ),
)

# Auto-changeover between cooling and heating (issue #501, TP1X_DA-AC-DUCT).
# `status` is the same bare On/Off shape as ABSENCE_POWER_SAVING, so it's a
# switch without a live-confirmed write. The offsets carry no unit field, so
# they stay unitless read-only diagnostics.
AUTO_CHANGEOVER = Capability(
    href="/autochangeover/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="auto_changeover",
            field="status",
            icon="mdi:sun-snowflake-variant",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["autochangeover", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
        *tuple(
            SensorDesc(
                key=f"auto_changeover_{key}",
                field=field,
                icon="mdi:thermometer-lines",
                entity_category="diagnostic",
                enabled_default=False,
                value_fn=_num,
            )
            for key, field in (
                ("cool_primary_offset", "coolPrimaryOffset"),
                ("cool_secondary_offset", "coolSecondaryOffset"),
                ("heat_primary_offset", "heatPrimaryOffset"),
                ("heat_secondary_offset", "heatSecondaryOffset"),
            )
        ),
    ),
)


def _dual_setpoint_unit(rep):
    return normalize_temp_unit(rep.get("x.com.samsung.da.unit"), "°C")


# Separate cooling/heating setpoints (issue #501, TP1X_DA-AC-DUCT). The dump
# has status Off, so there's no evidence yet for how the climate entity's
# heat_cool range would be written; read-only until a dump with it on shows up.
DUAL_SETPOINT = Capability(
    href="/temperatures/dualsetpoint/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="dual_setpoint_enabled",
            field="status",
            icon="mdi:thermometer-auto",
            entity_category="diagnostic",
            value_fn=lambda v: v == "On",
        ),
        SensorDesc(
            key="dual_setpoint_cooling",
            field="x.com.samsung.da.desired",
            device_class="temperature",
            entity_category="diagnostic",
            unit_fn=_dual_setpoint_unit,
            value_fn=_num,
        ),
        SensorDesc(
            key="dual_setpoint_heating",
            field="x.com.samsung.da.desiredHeat",
            device_class="temperature",
            entity_category="diagnostic",
            unit_fn=_dual_setpoint_unit,
            value_fn=_num,
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
    # /airlevelcheck/vs/0 is deliberately NOT here either (PR #316):
    # despite this list's old description of it as "scheduler plumbing",
    # both the CAC and TP1X_DA_AC_RAC_01011 fixtures already carry real,
    # populated periodicSensingActivationState/autoExeState values here --
    # the AI-Purify feature air_purifier.AIR_LEVEL_CHECK already models,
    # reused below rather than reinvented.
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
    # TP1X_DA-AC-FAC-class-only (issue #319) -- scoped here rather than
    # promoted to the global ignore list since it'd collide with families
    # that do bind some of these. Only /dnd/autosleep/vs/0 has a precedent
    # (air_purifier.COVERAGE ignores the same href for the same reason);
    # the rest are new, each with its own reason below.
    "/dnd/autosleep/vs/0",  # every field its inert default; needs a schedule editor
    "/outdoorsharing/vs/0",  # empty on this dump -- outdoor-unit sharing plumbing
    "/lifestyle/survey/vs/0",  # {list: [""]} placeholder, nothing to expose
    # supportedVoices carries opaque numeric voice-pack IDs ("100"/"101")
    # with no live current-selection field and, unlike SOUND_MODE's
    # self-descriptive mute/tone/voice codes, no confirmed human-readable
    # meaning to expose them under -- don't guess.
    "/settings/sound/voice/vs/0",
    # rssi/wifiFrequency (network housekeeping); lastEnergySavingTime and
    # cleaningStartTime are inert '1900-01-00' placeholders on this dump;
    # absenceInfo is an unconfirmed 48-slot P/A history blob with no
    # documented meaning -- don't guess what it encodes.
    "/csi/information/vs/0",
]

# Built as bare no-entity caps; folded into the AC registry (not global).
# HREF_TEMP_CURRENT and HREF_TEMPS_VS are excluded -- CURRENT_TEMPERATURE /
# CURRENT_TEMPERATURE_VS above already cover those with real entities.
COVERAGE = [
    Capability(href=h, poll_tier="warm")
    for h in CLIMATE_CONSUMED_HREFS
    if h not in (HREF_TEMP_CURRENT, HREF_TEMPS_VS)
] + [Capability(href=h) for h in _AC_IGNORED]

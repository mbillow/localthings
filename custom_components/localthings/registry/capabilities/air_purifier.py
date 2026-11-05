"""Capabilities for the Samsung ARTIK051_TVTL-class air purifier family
(model AX60R5080WD/SE, issue #56).

Power, kids-lock, remote-control, alarms, and the energy meter are the
shared common.py capabilities; /diagnosis/vs/0 reuses dishwasher.DIAGNOSIS
(identical field/write contract).

/mode/vs/0's options[] packs several '<Prefix>_<value>' flags, the same
packed-list contract as laundry.py's option_value/option_write. Light_On/
Light_Off is a real on/off switch here -- NOT the same polarity as the AC
family's own Light_On/Light_Off token on its own /mode/vs/0, which is
inverted (airconditioner._display_light_on). Comode_Off reads 'Off' on
every setting (Auto/Sleep/Low/Medium/High), ruling out the original
"fan speed selector" guess; exposed read-only. OptionCode_* and Blooming_*
are unmodeled: confirmed opaque / not app-facing.

/airflow/0's `speed` is a real fan-speed control: two independent units,
sampled 60-90s apart per setting, confirmed a clean monotonic 0-4 mapping
across Auto/Sleep/Low/Medium/High. AIRFLOW_GENERIC below builds a fan off
that range, with Auto and Sleep as presets. /airflow/vs/0's vendor `speedLevel` is
NOT used for the same purpose -- unreliable on both units in the same
round (collided Low/Medium on one, stuck at 0 on the other).
"""

import datetime

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    FanDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    TimeDesc,
)
from .common import (
    epoch_to_utc,
    filter_reset_button,
    filter_usage_percent,
    has_sensor_type,
    int_or_none,
    sensor_item_value,
)
from .laundry import bool_option_exists, bool_option_value, option_value, option_write

# Newer TP1X_DA-AC-AIR-class boards (issue #130) report fan modes directly
# on /mode/vs/0's top-level modes/supportedModes instead of packing
# everything into options[] like the older ARTIK051_TVTL family. Both
# generations share this href; FAN and MODE below are mutually exclusive
# via presence of supportedModes.
HREF_MODE = "/mode/vs/0"
HREF_AIRFLOW = "/airflow/0"
HREF_WIND_STRENGTH = "/wind/strength/vs/0"


def _has_top_level_modes(rep, resources):
    return isinstance(rep.get("x.com.samsung.da.supportedModes"), (list, tuple))


# Columns: key, icon, device item type, state_class, device_class, unit.
# state_class is what makes Home Assistant keep long-term statistics --
# without one, a reading is only in the short-term recorder history and
# disappears with the next purge (10 days by default), so it can't back a
# long-range air-quality graph. The values are already numeric
# (sensor_item_value returns int); three sensors in this same module
# (filter_progress, fan_speed_level, hepa_filter_usage) already declare one.
#
# Only the three particulate readings get it. They fall monotonically with
# particle size on three independent board families -- 11/9/5 on ARTIK051_TVTL
# (issue #56), 10/9/6 on AVT-WW-TP1 (issue #190), 18/14/9 on the range hood --
# which is concentration behaviour, and an average over time is meaningful for
# it. Odor and CleanLevel read 0-2 on every fixture and look like graded
# indices instead, where the mean of a grade isn't obviously meaningful; left
# without a state_class rather than guessing.
#
# device_class/unit: Dust=PM10, FineDust=PM2.5, SuperFineDust=PM1, all
# μg/m³ (issue #325). Three independent lines, none of them naming order --
# which is what the earlier "plausible but unconfirmed" note rejected:
#
#  1. The device grades its own readings. Each dust item's value[] is
#     [concentration, grade] (see common.sensor_item_value); the grade band
#     is not shared across the three fields -- a reading of 18 grades one
#     step *above* the floor as SuperFineDust (air_monitor fixture) but *at*
#     the floor as Dust (range_hood fixture), both 1-based families. So the
#     firmware itself treats them as three different scales ordered
#     coarse-to-fine, rather than one repeated measurement.
#  2. Where each field's floor/second-band boundary falls brackets the
#     Korean CAI bands: Dust good at 18, graded up at 31 (CAI PM10 breaks
#     at 30/31); FineDust good at 14, graded up at 23 (CAI PM2.5 breaks at
#     15/16); SuperFineDust good at 9, graded up at 18 (PM2.5-style, which
#     is what a PM1 reading gets -- there is no standard PM1 index).
#  3. A live ARTIK051_TVTL read against the SmartThings app at the same
#     moment: Dust matched the app's PM10 exactly, the other two were 1
#     μg/m³ off in the same order, and the app shows exactly these three
#     tiers, so there is no fourth candidate to assign.
#
# Dust >= FineDust >= SuperFineDust holds on all 11 fixtures that report
# this resource, which is the cumulative-mass ordering PM10 >= PM2.5 >= PM1
# requires by definition. The unit literal must stay HA's own spelling of
# μg/m³ (U+03BC GREEK SMALL LETTER MU, not U+00B5 MICRO SIGN) -- they render
# alike but only U+03BC is in DEVICE_CLASS_UNITS, and the mismatch is a
# runtime warning per entity, not a test failure. Pinned by
# tests/test_sensor_device_class_units.py.
_AIR_QUALITY_SENSORS = (
    ("dust", "mdi:blur", "Dust", "measurement", "pm10", "μg/m³"),
    ("fine_dust", "mdi:blur", "FineDust", "measurement", "pm25", "μg/m³"),
    ("super_fine_dust", "mdi:blur", "SuperFineDust", "measurement", "pm1", "μg/m³"),
)

# Samsung's dust health-concern capabilities enumerate `unknown`, `good`,
# `moderate`, `slightlyUnhealthy`, 'unhealthy`, `veryUnhealthy`, and
# `hazardous`. The tested purifier does not report a `slightlyUnhealthy`
# grade, instead skipping from 2 = `moderate` to 3 = `unhealthy`, so
# 'slightlyUnhealthy' is omitted. It is assumed that a grade of 5 will
# be reported as `hazardous`, but this is unconfirmed.


_HEALTH_CONCERN_STATES = (
    "unknown",
    "good",
    "moderate",
    "unhealthy",
    "very_unhealthy",
    "hazardous",
)


def _purifier_is_off_or_sleep(resources):
    airflow = resources.get(HREF_AIRFLOW) or {}
    if str(airflow.get("speed")) == "1":
        return True

    power = resources.get("/power/0") or {}
    if power.get("value") is False:
        return True

    power = resources.get("/power/vs/0") or {}
    return str(power.get("x.com.samsung.da.power")).lower() == "off"


def _air_quality_value(items, resources, sensor_type):
    value = sensor_item_value(items, sensor_type)
    return None if value == 0 and _purifier_is_off_or_sleep(resources) else value


def _grade_value(items, sensor_type, states, index=0):
    value = sensor_item_value(items, sensor_type, index)
    if value is None or value in (0, 10):
        return None
    if 0 < value < len(states):
        return states[value]
    return str(value)


def _has_grade(sensor_type):
    def exists(rep, resources):
        return is_stub_rep(rep) or any(
            isinstance(item, dict)
            and item.get("x.com.samsung.da.type") == sensor_type
            and isinstance(values := item.get("x.com.samsung.da.value"), (list, tuple))
            and len(values) > 1
            for item in (rep.get("x.com.samsung.da.items") or [])
        )

    return exists


AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="warm",
    entities=(
        *(
            SensorDesc(
                key=key,
                field="x.com.samsung.da.items",
                icon=icon,
                state_class=state_class,
                device_class=device_class,
                unit=unit,
                # Gated on the board listing the type: a purifier reporting
                # only CleanLevel (AVT-WW-TP1-22-TOUCHOTN, issue #414) used
                # to grow four permanently-unknown particulate sensors.
                # Listing a type isn't proof the hardware is real (issue
                # #166) -- that's a separate problem, and not one worth
                # hiding a working reading over.
                exists_fn=has_sensor_type(sensor_type),
                value_fn=lambda items, t=sensor_type: (
                    None if (value := sensor_item_value(items, t)) == 0 else value
                ),
                resources_value_fn=lambda items, resources, t=sensor_type: (
                    _air_quality_value(items, resources, t)
                ),
            )
            for key, icon, sensor_type, state_class, device_class, unit in _AIR_QUALITY_SENSORS
        ),
        SensorDesc(
            key="odor",
            field="x.com.samsung.da.items",
            icon="mdi:scent",
            exists_fn=has_sensor_type("Odor"),
            value_fn=lambda items: (
                None if (value := sensor_item_value(items, "Odor")) == 0 else value
            ),
        ),
        SensorDesc(
            key="clean_level",
            field="x.com.samsung.da.items",
            icon="mdi:air-filter",
            device_class="enum",
            options=_HEALTH_CONCERN_STATES[1:],
            exists_fn=has_sensor_type("CleanLevel"),
            value_fn=lambda items: _grade_value(
                items, "CleanLevel", _HEALTH_CONCERN_STATES
            ),
        ),
        *(
            SensorDesc(
                key=key,
                field="x.com.samsung.da.items",
                icon="mdi:blur",
                device_class="enum",
                options=_HEALTH_CONCERN_STATES[1:],
                exists_fn=_has_grade(sensor_type),
                value_fn=lambda items, t=sensor_type: _grade_value(
                    items, t, _HEALTH_CONCERN_STATES, 1
                ),
            )
            for key, sensor_type in (
                ("dust_grade", "Dust"),
                ("fine_dust_grade", "FineDust"),
                ("super_fine_dust_grade", "SuperFineDust"),
            )
        ),
        # CO2 (issue #387) -- same field/shape air_monitor.SENSORS already
        # models with device_class='carbon_dioxide'/unit='ppm'. Gated on the
        # type being listed so boards that don't report it (every current
        # fixture) don't grow an empty entity. Disabled by default for the
        # same reason as airconditioner.AIR_QUALITY (issue #166).
        SensorDesc(
            key="co2",
            field="x.com.samsung.da.items",
            icon="mdi:molecule-co2",
            device_class="carbon_dioxide",
            state_class="measurement",
            unit="ppm",
            exists_fn=has_sensor_type("CO2"),
            enabled_default=False,
            value_fn=lambda items: sensor_item_value(items, "CO2"),
        ),
    ),
)


def _consumable_state(items, name):
    """Read a `/consumable/vs/0`-style items[] entry -- {name, state} pairs,
    unlike AIR_QUALITY's {type, value} shape above."""
    for item in items or ():
        if isinstance(item, dict) and item.get("x.com.samsung.da.name") == name:
            return item.get("x.com.samsung.da.state")
    return None


# FilterProgress counts UP as the filter wears (100 = "needs changing",
# confirmed via the SmartThings app) -- named after the raw field rather
# than "filter life," which would imply the opposite direction.
FILTER = Capability(
    href="/consumable/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="filter_progress",
            field="x.com.samsung.da.items",
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=lambda items: int_or_none(_consumable_state(items, "FilterProgress")),
        ),
    ),
)

DEVICE_ACTIVE = Capability(
    href="/devicespecificinfo/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="device_active",
            field="x.com.samsung.da.deviceActive",
            icon="mdi:check-network-outline",
            entity_category="diagnostic",
            value_fn=lambda v: bool(v),
        ),
    ),
)


def _power_write(power_href, value):
    """Shared 'power' payload handling for this family's FanDescs -- targets
    whichever power href fan.py picked (the board may only report
    /power/0)."""
    if power_href == "/power/0":
        return ["power", "0"], {"value": bool(value)}
    return (["power", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"})


def _airflow_fan_write(payload, rep, href=None):
    kind, value, *args = payload
    if kind == "power":
        return _power_write(args[0] if args else "/power/vs/0", value)
    if kind == "speed":
        return ["airflow", "0"], {"speed": int(value)}
    return None


# The 0-4 code (see module docstring) backs a fan with Low/Medium/High as
# speeds and Auto/Sleep as presets (fan.LocalThingsAirflowFan, #658).
# `direction` stays a diagnostic: every dump reads 'Off' regardless of fan
# setting.
#
# Keyed 'airflow_fan', not 'fan' -- FAN below shares this registry and also
# uses key 'fan'; unique_id is built from key alone, so a shared key would
# collide if a board ever reported both (empirically mutually exclusive,
# not architecturally enforced the way same-href caps are).
AIRFLOW_GENERIC = Capability(
    href=HREF_AIRFLOW,
    poll_tier="warm",
    entities=(
        FanDesc(
            key="airflow_fan",
            translation_key="air_purifier_fan",
            field="speed",
            write_fn=_airflow_fan_write,
        ),
        SensorDesc(
            key="fan_direction",
            field="direction",
            icon="mdi:rotate-3d-variant",
            entity_category="diagnostic",
        ),
    ),
)

# Read-only fallback: speedLevel is unreliable (see module docstring),
# unlike /airflow/0's speed.
AIRFLOW_VS_FALLBACK = Capability(
    href="/airflow/vs/0",
    match_fn=lambda rep, resources: "/airflow/0" not in resources,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="fan_speed_level",
            field="x.com.samsung.da.speedLevel",
            icon="mdi:fan",
            state_class="measurement",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="fan_direction",
            field="x.com.samsung.da.direction",
            icon="mdi:rotate-3d-variant",
            entity_category="diagnostic",
        ),
    ),
)


def _light_write(payload, rep, href=None):
    # option_write's single-token merge is confirmed on a washer's
    # /course/vs/0 (issue #54); extrapolated here on the assumption the
    # same vendor field merges the same way on this family's /mode/vs/0.
    return ["mode", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Light", payload),
    }


MODE = Capability(
    href="/mode/vs/0",
    poll_tier="warm",
    match_fn=lambda rep, resources: not _has_top_level_modes(rep, resources),
    entities=(
        SwitchDesc(
            key="display_light",
            icon="mdi:led-on",
            entity_category="config",
            rep_fn=bool_option_value("Light"),
            exists_fn=bool_option_exists("Light"),
            write_fn=_light_write,
        ),
        # Read-only -- confirmed NOT the fan-speed selector (see module
        # docstring), actual purpose still unconfirmed.
        SensorDesc(
            key="operating_mode",
            icon="mdi:fan",
            entity_category="diagnostic",
            rep_fn=lambda rep: option_value(rep.get("x.com.samsung.da.options"), "Comode"),
            exists_fn=bool_option_exists("Comode"),
        ),
    ),
)


def _fan_write(payload, rep, href=None):
    kind, value, *args = payload
    if kind == "power":
        return _power_write(args[0] if args else "/power/vs/0", value)
    if kind == "mode":
        return ["mode", "vs", "0"], {"x.com.samsung.da.modes": [value]}
    return None


def _first_fan_mode(rep):
    """Representative scalar for the flattened golden state; the real
    entity reads live coordinator state instead."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


# Named preset modes (Smart/Max/Mid/WindFree/Sleep), not an ordered
# percentage -- these are named behaviors, not "faster/slower" positions,
# so fan.py only exposes PRESET_MODE here.
FAN = Capability(
    href=HREF_MODE,
    poll_tier="warm",
    match_fn=_has_top_level_modes,
    entities=(
        FanDesc(
            key="fan",
            translation_key="air_purifier_fan",
            rep_fn=_first_fan_mode,
            write_fn=_fan_write,
        ),
    ),
)


def _wind_strength_fan_write(payload, rep, href=None):
    kind, value, *args = payload
    if kind == "power":
        return _power_write(args[0] if args else "/power/vs/0", value)
    if kind == "mode":
        return ["wind", "strength", "vs", "0"], {"x.com.samsung.da.modes": value}
    return None


# A-VTWW-TP2-21-COMMON (issue #151): named presets like FAN above, but on a
# distinct href with numeric codes ("87"/"89"/"90"/"91") instead of
# self-describing supportedModes -- x.com.samsung.da.modesName gives the
# real names, read live by fan.py rather than a hardcoded map. `modes` is a
# bare string here, not a single-element list like HREF_MODE's.
#
# key is 'wind_strength_fan', not 'fan' -- same unique_id collision hazard
# as AIRFLOW_GENERIC above.
WIND_STRENGTH_FAN = Capability(
    href=HREF_WIND_STRENGTH,
    poll_tier="warm",
    entities=(
        FanDesc(
            key="wind_strength_fan",
            translation_key="air_purifier_fan",
            field="x.com.samsung.da.modes",
            write_fn=_wind_strength_fan_write,
        ),
    ),
)

# TP1X_DA-AC-AIR-class additions (issue #130): resources the older
# ARTIK051_TVTL family never reported.

# Screen/indicator panel on/off, distinct from the display_light switch
# above (ambient mood light) -- two independent controls on separate hrefs
# with the same {mode, supportedModes: [On, Off]} shape.
DISPLAY = Capability(
    href="/display/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="display",
            field="mode",
            icon="mdi:monitor",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["display", "vs", "0"],
                {"mode": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Same filterUsage/filterCapacity/filterStatus shape as the AC family's
# AIR_FILTER; the normal/wash/replace option list is reused as-is.
HEPA_FILTER = Capability(
    href="/filter/hepafilter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="hepa_filter_usage",
            rep_fn=filter_usage_percent,
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="hepa_filter_status",
            field="x.com.samsung.da.filterStatus",
            device_class="enum",
            options=("normal", "wash", "replace"),
            translation_key="filter_status",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
        filter_reset_button("hepa_filter_reset", "/filter/hepafilter/vs/0"),
    ),
)

# Physical panel/cover status ('Close' seen, plausibly the HEPA-filter
# cover) -- unconfirmed, and no supportedStatus list to check against, so a
# plain diagnostic rather than an asserted binary_sensor.
PANEL_STATUS = Capability(
    href="/panel/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="panel_status",
            field="status",
            icon="mdi:archive-outline",
            entity_category="diagnostic",
        ),
    ),
)

# Pet-care filter mode -- a plain On/Off field with no vendor prefix, same
# convention as airconditioner.MUTE_ONCE.
PET_FILTER_ACTIVATION = Capability(
    href="/petfilteractivation/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="pet_filter_activation",
            field="status",
            icon="mdi:paw",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["petfilteractivation", "vs", "0"],
                {"status": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# Sound mode/volume look like laundry.py's SOUND_MODE/SOUND_VOLUME but this
# board's actual values differ (supportedModes here is ['mute', 'buzzer'],
# not laundry's voice/tone/mute; volume is 0-3, not laundry's fixed 0-15) --
# separate descriptors reading live supported values instead of reusing
# laundry's hardcoded table.
SOUND_MODE = Capability(
    href="/settings/sound/mode/vs/0",
    poll_tier="cold",
    entities=(
        # Distinct translation_key from laundry.SOUND_MODE's shared
        # 'sound_mode' catalog ({voice, tone, mute}) -- this board's
        # {mute, buzzer} doesn't overlap it.
        SelectDesc(
            key="sound_mode",
            translation_key="air_purifier_sound_mode",
            field="mode",
            icon="mdi:volume-high",
            entity_category="config",
            options_field="supportedModes",
            write_fn=lambda p, rep, href=None: (
                ["settings", "sound", "mode", "vs", "0"],
                {"mode": p},
            ),
        ),
    ),
)

# Read-only descriptor of which sound output the unit has -- only one value
# seen, no alternatives to select between.
SOUND_OUTPUT = Capability(
    href="/settings/sound/output/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="sound_output",
            field="deviceType",
            icon="mdi:volume-high",
            entity_category="diagnostic",
        ),
    ),
)

SOUND_VOLUME = Capability(
    href="/settings/sound/volume/vs/0",
    poll_tier="cold",
    entities=(
        NumberDesc(
            key="sound_volume",
            field="level",
            icon="mdi:volume-medium",
            entity_category="config",
            # Some boards (issue #319's AC) report minLevel/resolution but no
            # maxLevel -- native_max_fn would silently collapse to 0, giving a
            # slider with no real range instead of no entity at all.
            exists_fn=lambda rep, resources: "maxLevel" in rep,
            native_min_fn=lambda rep: int_or_none(rep.get("minLevel")) or 0,
            native_max_fn=lambda rep: int_or_none(rep.get("maxLevel")) or 0,
            step_fn=lambda rep: int_or_none(rep.get("resolution")) or 1,
            value_fn=int_or_none,
            write_fn=lambda p, rep, href=None: (
                ["settings", "sound", "volume", "vs", "0"],
                {"level": str(int(p))},
            ),
        ),
    ),
)

# AI Purify -- /airlevelcheck/vs/0 (issues #84, #190). Not scheduler
# plumbing: it drives the SmartThings app's "AI Purify" feature (the unit
# wakes on a timer, samples air, optionally acts). Reported with the same
# field names by three of this registry's four board families (TP1X_DA-AC-AIR
# #130, A-VTWW-TP2 #151, AVT-WW-TP1 #84/#190); ARTIK051_TVTL has no such
# href. Bound unconditionally since it's safe to no-op where absent.
#
# Two independent knobs, one entity each rather than folded into one
# select: periodicSensingActivationState (is it running) and autoExeState
# (what it does with a bad reading, Off/Airpurify/Alarm) -- mirrors the
# appliance's own UI. Folding them lost information: a configured action
# became invisible while off, and no option could toggle the feature
# without also overwriting the action. The two "off"s are NOT
# interchangeable: the switch's off stops sampling entirely; the select's
# "Off" keeps sampling but doesn't act on it (the app calls that
# "sensing only").
#
# range_hood.AIR_LEVEL_CHECK models the same href's read-only fields
# (reused verbatim below) but is deliberately not imported: it exposes
# periodic_air_sensing as a read-only BinarySensorDesc where this board
# needs it writable, and reusing it would migrate every hood user's entity
# to a different platform.
#
# Every write below was exercised on AVT-WW-TP1-23-AXX500 hardware and
# verified by surviving a reconnect (this board 2.04s writes it silently
# discards, so an echo proves nothing). The other two families get the same
# writes on field-shape grounds only.
#
# Deferred: startSensingOnce looks like a one-shot "sense now" trigger but
# stays unbound until its side effect (not just the echo) is confirmed.


def _interval_minutes(seconds):
    """Device stores the interval in seconds; the entity is in minutes.
    Rounds up (not to nearest) so a sub-minute value can't floor to 0."""
    secs = int_or_none(seconds)
    if secs is None:
        return None
    return -(-secs // 60) if secs > 0 else 0


def _interval_write(payload, rep, href=None):
    # Minutes in the UI -> seconds on the wire. Modeled as a free Number,
    # not the app's three fixed choices, since the resource advertises no
    # constraint for this field (unlike supportedAutoExeState beside it)
    # and accepts finer values than the app offers (60s drove an observed
    # ~60s sensing cycle on hardware). One-minute floor matches this
    # board's own reporting resolution (lastSensingTime lands on exact
    # minutes). Zero is refused: unlike a real "no timer" 0 elsewhere in
    # this repo, nothing establishes what 0 does here. Silent no-op via
    # None, same shape as range_hood._lamp_level_write.
    minutes = round(float(payload))
    if minutes < 1:
        return None
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingInterval": str(minutes * 60)
    }


def _periodic_sensing_write(payload, rep, href=None):
    # Master on/off; leaves autoExeState alone so the configured action
    # survives the feature being toggled off -- the select can't do that,
    # since every option write sets an action too.
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingActivationState": ("On" if payload == "On" else "Off")
    }


def _skip_status_write(payload, rep, href=None):
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingSkipStatus": ("On" if payload == "On" else "Off")
    }


# Daily skip window, stored as one HHMMHHMM string
# (periodicSensingSkipTime). Cross-confirmed on two units (inert
# '00000000' vs a real '03002300'). Split into two HA time entities; each
# write reads the other half back out of the live rep so the pair
# round-trips -- confirmed in both directions on hardware.
def _skip_time_read(part):
    def _read(value):
        raw = str(value or "")
        chunk = raw[0:4] if part == "start" else raw[4:8]
        if len(chunk) == 4 and chunk.isdigit():
            try:
                return datetime.time(int(chunk[:2]), int(chunk[2:]))
            except ValueError:
                return None
        return None

    return _read


def _skip_half(raw, part):
    """The half this write isn't setting, normalized. An unparseable half
    becomes '0000' rather than carrying a malformed value back to the
    device."""
    chunk = (str(raw or "") + "00000000")[:8]
    other = chunk[4:8] if part == "start" else chunk[0:4]
    return other if _skip_time_read("end" if part == "start" else "start")(chunk) else "0000"


def _skip_time_write(part):
    def _write(value, rep, href=None):
        raw = rep.get("x.com.samsung.da.periodicSensingSkipTime", "")
        hhmm = f"{value.hour:02d}{value.minute:02d}"
        other = _skip_half(raw, part)
        new = hhmm + other if part == "start" else other + hhmm
        return ["airlevelcheck", "vs", "0"], {"x.com.samsung.da.periodicSensingSkipTime": new}

    return _write


AIR_LEVEL_CHECK = Capability(
    href="/airlevelcheck/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="periodic_air_sensing",
            field="x.com.samsung.da.periodicSensingActivationState",
            icon="mdi:radar",
            entity_category="config",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_periodic_sensing_write,
        ),
        # Options come off supportedAutoExeState rather than a typed table,
        # so an unrecognized fourth value still reaches the user.
        SelectDesc(
            key="sensing_mode",
            field="x.com.samsung.da.autoExeState",
            options_field="x.com.samsung.da.supportedAutoExeState",
            translation_key="sensing_mode",
            icon="mdi:radar",
            entity_category="config",
            write_fn=lambda p, rep, href=None: (
                ["airlevelcheck", "vs", "0"],
                {"x.com.samsung.da.autoExeState": p},
            ),
        ),
        # The one field that varies across families: TP1X_DA-AC-AIR (#130)
        # omits it, so that board runs sensing on a fixed, unexposed
        # interval.
        NumberDesc(
            key="sensing_interval",
            field="x.com.samsung.da.periodicSensingInterval",
            icon="mdi:timer-cog",
            entity_category="config",
            native_min=1,
            native_max=60,
            step=1,
            unit="min",
            exists_fn=lambda rep, resources: "x.com.samsung.da.periodicSensingInterval" in rep,
            value_fn=_interval_minutes,
            write_fn=_interval_write,
        ),
        SwitchDesc(
            key="periodic_sensing_skip_status",
            field="x.com.samsung.da.periodicSensingSkipStatus",
            icon="mdi:sleep",
            entity_category="config",
            value_fn=lambda v: str(v).lower() == "on",
            write_fn=_skip_status_write,
        ),
        TimeDesc(
            key="sensing_skip_start",
            field="x.com.samsung.da.periodicSensingSkipTime",
            icon="mdi:clock-start",
            entity_category="config",
            value_fn=_skip_time_read("start"),
            write_fn=_skip_time_write("start"),
        ),
        TimeDesc(
            key="sensing_skip_end",
            field="x.com.samsung.da.periodicSensingSkipTime",
            icon="mdi:clock-end",
            entity_category="config",
            value_fn=_skip_time_read("end"),
            write_fn=_skip_time_write("end"),
        ),
        # Read-only status, same keys as range_hood.AIR_LEVEL_CHECK.
        SensorDesc(
            key="air_sensing_state",
            field="x.com.samsung.da.sensingState",
            icon="mdi:radar",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="last_air_sensing_time",
            field="x.com.samsung.da.lastSensingTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=epoch_to_utc,
        ),
        # 'Kr1' on both dumps -- a region-prefixed, undocumented grade;
        # stays a raw diagnostic rather than an asserted enum.
        SensorDesc(
            key="last_air_sensing_level",
            field="x.com.samsung.da.lastSensingLevel",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
    ),
)

# Tower models carry a "booster" head the compact TP1X_DA-AC-AIR sibling
# doesn't have (AX100DB900EDD, issue #441): a second fan, a mood light and an
# oscillating outlet, one href each. Every selectable field names its own
# supported values, so all three selects read them live rather than from a
# tuple. The writes are inferred from those lists and the single-field
# {field: value} shape every other vs resource on this board uses; issue #441
# asks the reporter to exercise them on hardware.
BOOSTER_FAN_MODE = Capability(
    href="/booster/fanmode/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="booster_fan_mode",
            field="fanMode",
            icon="mdi:fan",
            options_field="supportedFanModes",
            write_fn=lambda p, rep, href=None: (
                ["booster", "fanmode", "vs", "0"],
                {"fanMode": p},
            ),
        ),
    ),
)

BOOSTER_LIGHT = Capability(
    href="/booster/light/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="booster_light",
            field="light",
            icon="mdi:led-strip-variant",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["booster", "light", "vs", "0"],
                {"light": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="booster_light_color_temperature",
            field="colorTemperature",
            icon="mdi:temperature-kelvin",
            entity_category="config",
            options_field="supportedColorTemperatures",
            write_fn=lambda p, rep, href=None: (
                ["booster", "light", "vs", "0"],
                {"colorTemperature": p},
            ),
        ),
        SelectDesc(
            key="booster_light_brightness",
            field="brightnessLevel",
            icon="mdi:brightness-6",
            entity_category="config",
            options_field="supportedBrightnessLevels",
            write_fn=lambda p, rep, href=None: (
                ["booster", "light", "vs", "0"],
                {"brightnessLevel": p},
            ),
        ),
        # Reads 'On' alongside brightnessLevel 'Smart', so what it gates isn't
        # clear from one dump -- shipped as a toggle the reporter can flip and
        # report back (issue #441), not as a confirmed control.
        SwitchDesc(
            key="booster_light_manual_brightness",
            field="manualBrightness",
            icon="mdi:brightness-percent",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["booster", "light", "vs", "0"],
                {"manualBrightness": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

BOOSTER_OSCILLATION = Capability(
    href="/booster/oscillation/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="booster_oscillation",
            field="oscillation",
            icon="mdi:arrow-oscillating",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["booster", "oscillation", "vs", "0"],
                {"oscillation": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="booster_oscillation_angle",
            field="oscillationAngle",
            icon="mdi:angle-acute",
            options_field="supportedOscillationAngles",
            write_fn=lambda p, rep, href=None: (
                ["booster", "oscillation", "vs", "0"],
                {"oscillationAngle": p},
            ),
        ),
        # '2' while oscillationAngle reads 'Circulation', the second entry of
        # supportedOscillationAngles -- suggestive of a 1-based index into that
        # list, but one sample can't tell that from a head position. Raw
        # diagnostic until a dump at a different angle says which (issue #441).
        SensorDesc(
            key="booster_angle_location",
            field="angleLocation",
            icon="mdi:compass-outline",
            entity_category="diagnostic",
        ),
    ),
)


# /humidity/0 and /humidity/vs/0 are empty on both dumps -- covered here
# (not globally) since they collide with fridge/AC schemas elsewhere, same
# reasoning as airconditioner.py's _AC_IGNORED. The next six hrefs (issue
# #130) are the exact same DA-AC- board resources as _AC_IGNORED,
# duplicated here rather than promoted to the global list (a possible
# follow-up DRY cleanup).
COVERAGE = [
    Capability(href="/humidity/0"),
    Capability(href="/humidity/vs/0"),
    Capability(href="/availablecontrolsets/vs/0"),  # opaque hex-encoded control-set bitmap
    Capability(href="/da/softreset/vs/0"),  # soft-reset trigger plumbing
    Capability(href="/keepnormalstate/vs/0"),  # internal keep-normal flag
    Capability(href="/personality/presence/vs/0"),  # presence-personalization plumbing (empty here)
    Capability(href="/reserverulesets/vs/0"),  # opaque hex-encoded schedule reservation blob
    # Do-not-disturb/auto-sleep schedule -- every field reads its inert
    # default on the only dump seen. Needs a multi-field schedule editor,
    # same as fridge.py's /defrost/reservation/vs/0.
    Capability(href="/dnd/autosleep/vs/0"),
    # Empty on the A-VTWW-TP2-21 dump (issue #151) -- this board's
    # convenient-mode equivalent lives in WIND_STRENGTH_FAN instead.
    Capability(href="/mode/convenient/vs/0"),
]

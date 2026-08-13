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
across Auto/Sleep/Low/Medium/High. AIRFLOW_GENERIC below builds an
ordered-speed fan off that range. /airflow/vs/0's vendor `speedLevel` is
NOT used for the same purpose -- unreliable on both units in the same
round (collided Low/Medium on one, stuck at 0 on the other).
"""

import datetime

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
from .common import epoch_to_utc, filter_usage_percent, int_or_none, sensor_item_value
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
# device_class/unit confirmed on a live ARTIK051_TVTL against the SmartThings
# app at the same moment (issue #325): Dust=PM10, FineDust=PM2.5,
# SuperFineDust=PM1, all µg/m³. Dust matched PM10 exactly; the other two
# were 1 µg/m³ off the app, same order.
_AIR_QUALITY_SENSORS = (
    ("dust", "mdi:blur", "Dust", "measurement", "pm10", "µg/m³"),
    ("fine_dust", "mdi:blur", "FineDust", "measurement", "pm25", "µg/m³"),
    ("super_fine_dust", "mdi:blur", "SuperFineDust", "measurement", "pm1", "µg/m³"),
    ("odor", "mdi:scent", "Odor", None, None, None),
    ("clean_level", "mdi:air-filter", "CleanLevel", None, None, None),
)

AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="warm",
    entities=tuple(
        SensorDesc(
            key=key,
            field="x.com.samsung.da.items",
            icon=icon,
            state_class=state_class,
            device_class=device_class,
            unit=unit,
            value_fn=lambda items, t=sensor_type: sensor_item_value(items, t),
        )
        for key, icon, sensor_type, state_class, device_class, unit in _AIR_QUALITY_SENSORS
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


# Confirmed monotonic 0-4 speed code (see module docstring) backs a real
# ordered-speed fan, same SET_SPEED shape as the range hood's. `direction`
# stays a diagnostic: every dump reads 'Off' regardless of fan setting.
#
# Keyed 'airflow_fan', not 'fan' -- FAN below shares this registry and also
# uses key 'fan'; unique_id is built from key alone, so a shared key would
# collide if a board ever reported both (empirically mutually exclusive,
# not architecturally enforced the way same-href caps are).
AIRFLOW_GENERIC = Capability(
    href=HREF_AIRFLOW,
    poll_tier="warm",
    entities=(
        FanDesc(key="airflow_fan", field="speed", write_fn=_airflow_fan_write),
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

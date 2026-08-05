"""Capabilities for the Samsung ARTIK051_TVTL-class air purifier family
(model AX60R5080WD/SE, issue #56).

Power, kids-lock, remote-control, alarms, and the energy meter are the shared
common.py capabilities (this family exposes the standard /power/0+/power/vs/0
pair and /alarms/vs/0, /energy/consumption/vs/0). /diagnosis/vs/0 reuses
dishwasher.DIAGNOSIS -- identical field/write contract
(x.com.samsung.da.diagnosisStart, 'Ready' on both dumps).

/mode/vs/0's x.com.samsung.da.options array packs multiple independent
'<Prefix>_<value>' flags into one list -- the same packed-list contract
laundry.py's option_value/option_write already model for /course/vs/0's
options[] (reused directly below, just against this family's own href). Per
issue #56's follow-up (five diagnostics dumps captured with the physical unit
set to Auto/Sleep/Low/Medium/High):
  Light_On / Light_Off  -- a plain on/off flag; MODE below models it as a
                            real switch, RMW-replacing just that one entry.
                            NOT the same polarity as the AC family's own
                            Light_On/Light_Off token on its own /mode/vs/0
                            (airconditioner._display_light_on) -- that one is
                            confirmed inverted (Light_Off means the panel is
                            lit) on live hardware. Same token name, same
                            resource name, different device type and
                            opposite meaning -- don't unify them.
  Comode_Off            -- read 'Off' on *every* one of the five dumps,
                            including High/Low/Medium/Auto -- confirms this
                            is NOT the fan-speed selector (ruling out the
                            original guess); exposed read-only since its
                            actual purpose is still unconfirmed.
  OptionCode_60282       -- confirmed opaque/not user-facing in the
                            SmartThings app; not modeled (same treatment as
                            range_hood's OptionCode_* token on the same
                            href).
  Blooming_*             -- confirmed to have no corresponding SmartThings
                            app setting; dropped entirely rather than kept
                            as an unexplained diagnostic (it did track 1:1
                            with Sleep mode across the five dumps -- 0 in
                            Sleep, 6 otherwise -- so it's plausibly an
                            automatic side effect of sleep mode, e.g. a
                            display-dimming level, but that's still a guess).

/airflow/0's `speed` is now a real fan-speed control (issue #56 follow-up).
The first round of five dumps above wasn't conclusive -- it read 0 for both
Auto *and* High, and 3 for Low/Medium *and* Sleep, likely because all five
were captured within about a minute of each other, faster than this
integration's own ~30s poll cycle could settle each change. A second round,
captured 60-90s apart per setting on two independent units, confirmed a
clean monotonic mapping instead: Auto=0, Sleep=1, Low=2, Medium=3, High=4.
AIRFLOW_GENERIC below builds an ordered-speed fan off that confirmed 0-4
range -- same SET_SPEED shape as range_hood.py's fan, mapping HA's
percentage steps straight onto the raw code, no named-preset table needed
(unlike the TP1X family's FAN, which exposes real named modes because its
board actually reports a supportedModes list to hang names off of).

/airflow/vs/0's vendor `speedLevel` is NOT used for the same purpose -- it
was unreliable on both units in that second round (Low/Medium collided on
one unit, stuck at 0 throughout on the other), so AIRFLOW_VS_FALLBACK below
stays a plain read-only diagnostic even after this change.
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

# Newer TP1X_DA-AC-AIR-class boards (e.g. TP1X_DA-AC-AIR-01031_0000, issue
# #130) report fan modes directly on /mode/vs/0's top-level `modes`/
# `supportedModes` fields (Smart/Max/Mid/WindFree/Sleep) instead of packing
# everything into the options[] array the way the older ARTIK051_TVTL
# family above does -- that older family's /mode/vs/0 has no top-level
# supportedModes at all (see the module docstring's Comode_Off finding).
# Both board generations share the /mode/vs/0 href, so FAN and MODE below
# are mutually exclusive via this presence check rather than colliding.
HREF_MODE = "/mode/vs/0"
HREF_AIRFLOW = "/airflow/0"
HREF_WIND_STRENGTH = "/wind/strength/vs/0"


def _has_top_level_modes(rep, resources):
    return isinstance(rep.get("x.com.samsung.da.supportedModes"), (list, tuple))


_AIR_QUALITY_SENSORS = (
    ("dust", "mdi:blur", "Dust"),
    ("fine_dust", "mdi:blur", "FineDust"),
    ("super_fine_dust", "mdi:blur", "SuperFineDust"),
    ("odor", "mdi:scent", "Odor"),
    ("clean_level", "mdi:air-filter", "CleanLevel"),
)

AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="warm",
    entities=tuple(
        SensorDesc(
            key=key,
            field="x.com.samsung.da.items",
            icon=icon,
            value_fn=lambda items, t=sensor_type: sensor_item_value(items, t),
        )
        for key, icon, sensor_type in _AIR_QUALITY_SENSORS
    ),
)


def _consumable_state(items, name):
    """Read a `/consumable/vs/0`-style items[] entry -- {name, state} pairs,
    unlike AIR_QUALITY's {type, value} shape above."""
    for item in items or ():
        if isinstance(item, dict) and item.get("x.com.samsung.da.name") == name:
            return item.get("x.com.samsung.da.state")
    return None


# FilterProgress is a 0-100 percentage counting up as the filter wears --
# confirmed via issue #56: the SmartThings app shows "Filter needs changing"
# once this reaches 100, so 100 means fully used, not "brand new." Named
# after the raw field (matching the AC/range_hood filterUsage convention,
# which counts the same direction) rather than "filter life," which would
# imply the opposite direction.
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
    """Shared 'power' payload handling for this family's three FanDesc write
    functions -- targets whichever power href fan.py's _power_payload picked
    (the board may only report /power/0); a hardcoded vendor href here would
    silently no-op on such a board even though the entity's own is_on
    already falls back to reading it correctly."""
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


# Confirmed via issue #56's second, properly-spaced round of diagnostics
# (two independent units, 60-90s apart per setting): /airflow/0's `speed` is
# a clean, monotonic 0-4 code across Auto/Sleep/Low/Medium/High, so it now
# backs a real ordered-speed fan (fan.py's LocalThingsAirflowFan, same
# SET_SPEED shape as the range hood's) instead of a read-only sensor --
# no named-preset table needed, since HA's percentage steps map onto the
# raw 0-4 code directly, the same way the range hood's numeric levels do.
# `direction` stays a plain diagnostic: every dump seen (both rounds, both
# units) reads 'Off' for it regardless of fan setting, so there's nothing
# confirmed to control there yet.
#
# Keyed 'airflow_fan', not 'fan' -- FAN below (bound to the shared
# /mode/vs/0 href) also uses 'fan', and BoundEntity's unique_id is built
# from key alone (entity.py's _key), not href. FAN and AIRFLOW_GENERIC are
# only *empirically* mutually exclusive (every dump seen has one board
# generation's shape or the other, never both), not architecturally
# enforced the way same-href caps are by _build()'s match_fn check -- a
# same key would collide if a future board ever reported both.
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

# Left exactly as a read-only fallback -- speedLevel is NOT the same
# confirmed-reliable field as /airflow/0's speed above (see module
# docstring): it collided Low/Medium on one unit and stuck at 0 throughout
# on the other in the same properly-spaced round.
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
    # option_write's single-token write is confirmed on a washer's
    # /course/vs/0 (issue #54), NOT independently on this family's
    # /mode/vs/0 -- extrapolated on the assumption the same vendor field
    # merges the same way everywhere. If some unit replaces the field
    # outright instead, this would drop Comode/OptionCode alongside it on
    # the next light toggle; revisit if a real device report surfaces that.
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
    """Representative scalar for the fan entity in the flattened state
    (golden/regression), mirroring airconditioner.py's own _first_mode --
    the real entity computes its state from live coordinator reads."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


# Named preset modes (Smart/Max/Mid/WindFree/Sleep), not an ordered
# percentage -- WindFree/Smart/Sleep are named behaviors, not
# "faster/slower" positions relative to Max/Mid, so fan.py's entity for
# this only exposes PRESET_MODE, matching how the AC family's own named
# convenient modes are modeled as a preset rather than a speed number.
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


# A-VTWW-TP2-21-COMMON (issue #151): named preset modes like FAN above, but
# on a distinct href with numeric codes ("87"/"89"/"90"/"91") instead of
# self-describing supportedModes -- x.com.samsung.da.modesName gives the
# actual names (SMART/MAX/WINDFREE/Sleep), read live by fan.py's
# LocalThingsAirPurifierFan._label_for_code rather than a hardcoded
# per-model map. modes here is a bare string ('87'), not a single-element
# list like HREF_MODE's -- _wind_strength_fan_write writes it back as-is.
#
# key is 'wind_strength_fan', NOT 'fan' -- FAN above shares this registry
# and also uses a FanDesc; BoundEntity's unique_id is built from key alone
# (entity.py's _key), not href, so two same-key FanDescs in one registry
# would collide if a board ever bound both (see AIRFLOW_GENERIC's own
# comment on this exact hazard -- missed here in the initial cut).
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

# ---------------------------------------------------------------------------
# TP1X_DA-AC-AIR-class additions (issue #130). This board reports several
# resources the older ARTIK051_TVTL family never did.
# ---------------------------------------------------------------------------

# Screen/indicator-panel on/off -- distinct from LIGHT below (ambient mood
# light): both report the same {mode, supportedModes: [On, Off]} shape on
# separate hrefs on this dump, so they're two independent physical controls,
# not a duplicate encoding of one.
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

# Same filterUsage/filterCapacity/filterStatus shape as the AC family's own
# AIR_FILTER (airconditioner.py) -- confirmed normal/wash/replace values not
# seen on this one dump, so the option list there is reused as-is rather
# than re-deriving it from a single sample.
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

# Physical panel/cover status -- meaning of the one value seen ('Close') is
# plausible (the HEPA-filter access cover) but unconfirmed, and no
# supportedStatus list is present to check against -- exposed as a plain
# diagnostic sensor rather than an asserted binary_sensor polarity.
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

# Sound mode/volume shapes look like laundry.py's SOUND_MODE/SOUND_VOLUME at
# a glance, but this board's actual values differ (supportedModes here is
# ['mute', 'buzzer'], not laundry's hardcoded voice/tone/mute; volume range
# is 0-3, not laundry's fixed 0-15) -- reusing those would either reject a
# valid write ('buzzer') or expose the wrong number range, so these are
# separate descriptors reading the live supported values instead of a
# hardcoded table.
SOUND_MODE = Capability(
    href="/settings/sound/mode/vs/0",
    poll_tier="cold",
    entities=(
        # Distinct translation_key from laundry.SOUND_MODE's shared
        # 'sound_mode' catalog entry -- that one's state table is
        # {voice, tone, mute}, but this board's supportedModes is
        # {mute, buzzer}. Sharing the key would leave 'buzzer' unlabelled
        # (falls through to the raw code) since the catalogs don't overlap.
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

# ---------------------------------------------------------------------------
# AI Purify -- /airlevelcheck/vs/0 (issues #84 and #190)
#
# Covered as "periodic air-quality sensing scheduler plumbing" until two dumps
# of the AVT-WW-TP1-23 board showed it is not plumbing: it drives the feature
# the SmartThings app calls AI Purify, where the unit wakes on a timer, samples
# the air, and optionally acts on the result. Every field is named, none are
# opaque, and two of them are already user-set on the reported units.
#
# Three of this registry's four board families report the resource with the
# same field names -- TP1X_DA-AC-AIR (#130), A-VTWW-TP2 (#151) and AVT-WW-TP1
# (#84, #190); only ARTIK051_TVTL (#56) has no such href. Bound unconditionally
# rather than behind a match_fn so any board reporting it is covered; the one
# field that genuinely varies is gated per-entity below.
#
# The resource carries two independent knobs and they get one entity each,
# rather than being folded into a single control:
#
#   periodicSensingActivationState  On/Off              -- is AI Purify running
#   autoExeState                    Off/Airpurify/Alarm -- what it does with a
#                                                          bad reading
#
# The appliance itself presents them that way: its own UI has an on/off for AI
# Purify separately from the three mode choices. Folding them into one select
# was tried first and lost two things -- a configured action became invisible
# while the feature was off, and no option could toggle the feature without
# also overwriting the action.
#
# Note the two 'off's mean opposite things and are not interchangeable. The
# switch's off stops the unit sampling at all; the select's off is the
# advertised autoExeState "Off", where the unit keeps sampling and simply
# doesn't act on what it measures -- the app calls that choice "sensing only".
#
# The select reads its options straight off supportedAutoExeState rather than
# a typed-in tuple, the same shape SOUND_MODE below uses for supportedModes: a
# board advertising a fourth action gets it accepted on both the options list
# and the write path.
#
# range_hood.AIR_LEVEL_CHECK already models this same href, and its read-only
# keys (air_sensing_state / last_air_sensing_time / last_air_sensing_level) are
# reused verbatim so both families share one catalog entry. It is deliberately
# NOT imported: the hood exposes periodic_air_sensing as a read-only
# BinarySensorDesc and this board needs a writable SwitchDesc on that same key,
# so reusing the hood's capability would migrate every hood user's entity to a
# different platform.
#
# Verification: every write below was exercised on AVT-WW-TP1-23-AXX500
# hardware. This board returns 2.04 for writes it silently discards (see
# HEPA_FILTER's filter-reset note), so an echo proves nothing -- each was
# judged by the value surviving a reconnect, which forces a new DTLS session,
# fresh discovery and a fresh observe of this href, leaving no cached state to
# read back. The other two families get the writes on field-shape grounds, the
# same basis on which they already share MODE, HEPA_FILTER and the air-quality
# sensors.
#
# Deferred: startSensingOnce (On/Off on all three dumps) looks like a one-shot
# "sense now" trigger and would be a ButtonDesc, but nothing here writes it yet
# and this board is known to acknowledge writes it discards -- so it stays
# unbound until someone can confirm the side effect rather than the echo.
# ---------------------------------------------------------------------------


def _interval_minutes(seconds):
    """Device stores the interval in seconds; the entity is in minutes.

    `is None` rather than a falsy check so a reported 0 is distinguishable
    from a missing one. Anything else nonzero rounds up rather than to
    nearest, so a sub-minute value can't render as 0 and fall below the
    entity's own floor.
    """
    secs = int_or_none(seconds)
    if secs is None:
        return None
    return -(-secs // 60) if secs > 0 else 0


def _interval_write(payload, rep, href=None):
    # Minutes in the UI -> seconds on the wire (scalar string). Modelled as a
    # free Number rather than the app's three fixed choices (10 min / 30 min /
    # 1 hour): this resource advertises no supported-values or range field for
    # the interval -- supportedAutoExeState sits right beside it, so the board
    # does advertise constraints where it has them -- and it accepts values the
    # app never offers. Writing 60 s, six times finer than the app's smallest
    # choice, drove an observed ~60 s sensing cycle on hardware.
    #
    # One minute is the floor because that's the resolution this board reports
    # results at: lastSensingTime lands on an exact minute on every sample from
    # the AVT-WW-TP1 and A-VTWW-TP2 boards (both fixtures, and eleven
    # consecutive live readings), where the TP1X/AC/hood boards report arbitrary
    # seconds. A sub-minute interval is therefore unobservable here whether or
    # not the board honours it. Zero is refused for a separate reason: unlike
    # oven.cook_time or operational's delay hours, where 0 is a real setting
    # ("no timer", "no delay"), nothing establishes what a 0 interval does to
    # this board -- so native_min stops the UI offering it, and this guard
    # covers the service-call path. Silent no-op via a None return, the same
    # shape range_hood._lamp_level_write uses for a level the device didn't
    # advertise.
    minutes = round(float(payload))
    if minutes < 1:
        return None
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingInterval": str(minutes * 60)
    }


def _periodic_sensing_write(payload, rep, href=None):
    # The master on/off for AI Purify. Leaves autoExeState alone, so the
    # configured action survives the feature being switched off and comes back
    # with it -- the thing the select cannot do, since every option it writes
    # sets an action.
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingActivationState": ("On" if payload == "On" else "Off")
    }


def _skip_status_write(payload, rep, href=None):
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingSkipStatus": ("On" if payload == "On" else "Off")
    }


# The daily window during which periodic sensing is skipped, stored as one
# HHMMHHMM string (start+end) on periodicSensingSkipTime. The read side is
# cross-confirmed on two units: issue #84's sits at the inert '00000000', while
# issue #190's carries a real user-set '03002300' -> 03:00-23:00. Split into
# two HA time entities; each write reads the other half back out of the live
# rep so the pair round-trips. Confirmed in both directions on hardware: from
# 13:00-23:00, writing start=07:30 then end=22:00 left the device holding
# '07302200' -- each write kept the half it wasn't given.
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
    """The half this write isn't setting, normalized. Padding alone would carry
    a malformed value straight back to the device -- writing start over a junk
    skip time would send '0730' + junk. The read side already refuses a half it
    can't parse, so an unparseable one becomes '0000' here and the pair
    round-trips honestly in the same cases."""
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
        # Options come off supportedAutoExeState, not a table here -- the
        # catalog carries the labels for the three values seen so far, and an
        # unrecognized fourth still reaches the user (select.py falls back to
        # the device's own token when the catalog doesn't know it).
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
        # The one field that varies across the three families reporting this
        # resource: the TP1X_DA-AC-AIR dump (#130) omits it while both
        # AVT-WW-TP1 dumps and the A-VTWW-TP2 dump carry it, so that board runs
        # the sensing engine on a fixed interval it doesn't expose.
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
        # 'Kr1' on both dumps -- a national air-quality grade whose scale is
        # region-prefixed and undocumented here, so it stays a raw diagnostic
        # rather than being mapped to an asserted enum.
        SensorDesc(
            key="last_air_sensing_level",
            field="x.com.samsung.da.lastSensingLevel",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
    ),
)

# /humidity/0 and /humidity/vs/0 are empty {} on both dumps this family has
# been verified against -- covered here (not globally, per ignored.py's
# module docstring) since those hrefs collide with fridge/AC schemas
# elsewhere. Same two hrefs and reasoning as airconditioner.py's _AC_IGNORED.
#
# The next six hrefs (issue #130, TP1X_DA-AC-AIR board) are the exact same
# resources, same shapes, same reasoning as airconditioner.py's
# _AC_IGNORED on the shared DA-AC- board family -- duplicated here rather
# than promoted to the global ignored.py list, since that would require
# also removing them from _AC_IGNORED in the same change (a global entry
# colliding with a family-local bare Capability on the same href raises in
# _build()); left as a possible follow-up DRY cleanup.
COVERAGE = [
    Capability(href="/humidity/0"),
    Capability(href="/humidity/vs/0"),
    Capability(href="/availablecontrolsets/vs/0"),  # opaque hex-encoded control-set bitmap
    Capability(href="/da/softreset/vs/0"),  # soft-reset trigger plumbing
    Capability(href="/keepnormalstate/vs/0"),  # internal keep-normal flag
    Capability(href="/personality/presence/vs/0"),  # presence-personalization plumbing (empty here)
    Capability(href="/reserverulesets/vs/0"),  # opaque hex-encoded schedule reservation blob
    # Do-not-disturb/auto-sleep schedule (visible/startTime/endTime/
    # useTimeSetting/functionState) -- every field reads its inert default
    # on the only dump seen (times both '00:00:00', useTimeSetting/
    # functionState both 'false'). Same "needs a multi-field schedule
    # editor" treatment as fridge.py's /defrost/reservation/vs/0.
    Capability(href="/dnd/autosleep/vs/0"),
    # Empty ({}) on the A-VTWW-TP2-21 dump (issue #151) -- this board's
    # convenient-mode-equivalent behavior lives entirely in WIND_STRENGTH_FAN
    # above instead.
    Capability(href="/mode/convenient/vs/0"),
]

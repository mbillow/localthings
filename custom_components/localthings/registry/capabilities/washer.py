"""Capabilities specific to washer appliances (Samsung DA_WM_TP1-class
front-load washers).

Resources verified against two live WW90DG6U25LEU4 dumps (Table_02 course
family). Washers share the `DA_WM_` laundry board with dryers, so their
`modelNum` can't tell the two apart -- see `registry/by_type/__init__.py`'s
`_CONSUMER_PREFIX_TO_KEY` for the `description`-based detection this device
type requires.

The shared laundry surface -- power/kids-lock/remote-control OCF+vendor
fallback pairs, buzzer, energy meter, job-beginning-status, and the
/course/vs/0 cycle-select machinery -- lives in laundry.py. Only washer-
specific controls (wash settings, drum-clean tracking, dispenser dosing) are
here; they read washer-only fields off the same shared /course/vs/0 options
array.
"""

from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc
from .laundry import (
    bool_option_exists,
    bool_option_switch,
    cycle_options,
    cycle_select,
    drum_clean_cycles_remaining,
    drum_clean_last_cleaned,
    hex_pairs,
    option_value,
    option_write,
    washer_cycle_fallback,
)

# Course_XX hex code labels (translations/en.json,
# washer_cycle_table_02.state.<id>) come from several devices, cross-checked
# rather than guessed: 23 codes from a live WW90DG6U25LEU4's editCourseList,
# matched positionally against a user's app screenshots and the printed
# manual (issue #2); 5 more (Wash+Dry, Air Wash, Cotton Dry, Synthetics Dry,
# a second distinct '1F' Intense Cold) from a WD90T654DBN/S1 combo's own
# editCourseList and screenshots (issue #22, a combo's own course set, not
# implying anything about a plain washer's '1F'); 3 more (Eco Cold, Towels,
# Self Clean+) verified directly on a WF50A8600AV/US by reading back the raw
# code after selecting each cycle on the appliance (issue #80). 2 more
# ('0A' Towels, 'B0' Mixed Load) reported for a WW90DG5G34ABLE on the same
# Table_02 family (issue #363). Several codes legitimately share a label
# across different course tables -- '21'/'65' Colors, '27'/'5E'/'78'
# Rinse+Spin, '0A'/'33'/'54'/'70' Towels -- not typos. (This list said
# "'24' Towels" until issue #343 found 24/33 transposed; 24 is Bedding.)
#
# No static fallback list is kept here: other models have different actual
# course sets, so hardcoding one device's list would show/hide the wrong
# options elsewhere. laundry.cycle_options() reads only the live
# x.com.samsung.da.editCourseList; a device that doesn't populate it gets no
# cycle select at all (see cycle_select's exists_fn). x.com.samsung.da.
# options' MostUsed_* entry was considered as a fallback source (its first
# byte matches the selected Course_XX on both dumps), but the remaining
# bytes don't decode to any confirmed course code, so it isn't used.
#
# The owner of a Korean Table_02 washer confirmed the names for its newer
# 69/6A-79/88 course-code family, including Course_69 as AI Wash. Those names
# live only in the table-scoped translation catalog; a code not confirmed by
# the owner or device metadata falls back to washer_cycle_fallback, which
# surfaces a personal-course name only -- no invented English label for an
# unrecognized standard code (PR #251 review).
#
# washer_cycle_table_00 (issue #357) is a separate, older course-code family
# reported by a WF45R6300AW/US -- confirmed by the reporter selecting each
# cycle on the appliance and reading back the raw code, the same method used
# for Table_02's WF50A8600AV/US codes above. A device reporting Table_00 with
# an unconfirmed code (FlexWash's washer_flexwash_device fixture, for
# instance) still renders that code raw rather than borrowing a Table_02
# label -- the two tables are unrelated code spaces despite a handful of
# overlapping hex values.
# ---------------------------------------------------------------------------

# /washer/vs/0 -- wash temperature, spin speed, rinse cycle count.
# Despite the shared href, this is unrelated to dryer.DRYER_SETTINGS (also
# bound to '/washer/vs/0') -- an artifact of Samsung reusing the same OCF
# path for different device families. Only one of the two ever binds for a
# given device, since dryer and washer are separate by_type registries.


def _enabled_write(field):
    def write(p, rep, href=None):
        if p not in ("On", "Off"):
            return None
        return ["washer", "vs", "0"], {field: p}

    return write


WASHER_SETTINGS = Capability(
    href="/washer/vs/0",
    entities=(
        SelectDesc(
            key="wash_temperature",
            field="x.com.samsung.da.waterTemperature",
            icon="mdi:thermometer-water",
            entity_category="config",
            options_field="x.com.samsung.da.supportedWaterTemperature",
            write_fn=lambda p, rep, href=None: (
                ["washer", "vs", "0"],
                {"x.com.samsung.da.waterTemperature": p},
            ),
        ),
        SelectDesc(
            key="spin_speed",
            field="x.com.samsung.da.spinLevel",
            icon="mdi:sync",
            entity_category="config",
            options_field="x.com.samsung.da.supportedSpinLevel",
            write_fn=lambda p, rep, href=None: (
                ["washer", "vs", "0"],
                {"x.com.samsung.da.spinLevel": p},
            ),
        ),
        SelectDesc(
            key="rinse_cycles",
            field="x.com.samsung.da.rinseCycles",
            icon="mdi:water-sync",
            entity_category="config",
            options_field="x.com.samsung.da.supportedRinseCycles",
            write_fn=lambda p, rep, href=None: (
                ["washer", "vs", "0"],
                {"x.com.samsung.da.rinseCycles": p},
            ),
        ),
        # Whether each reservoir auto-dispenses at all, as opposed to the
        # dose selects on /course/vs/0 below that set how much (issue #437,
        # whose washer reported detergent On and softener Off in one dump).
        # Self-gated: only boards with an auto-dispenser report the fields.
        SwitchDesc(
            key="auto_detergent",
            field="x.com.samsung.da.autoDetergentEnabled",
            icon="mdi:cup-water",
            entity_category="config",
            exists_fn=lambda rep, resources: "x.com.samsung.da.autoDetergentEnabled" in rep,
            value_fn=lambda v: v == "On",
            write_fn=_enabled_write("x.com.samsung.da.autoDetergentEnabled"),
        ),
        SwitchDesc(
            key="auto_softener",
            field="x.com.samsung.da.autoSoftenerEnabled",
            icon="mdi:flask-outline",
            entity_category="config",
            exists_fn=lambda rep, resources: "x.com.samsung.da.autoSoftenerEnabled" in rep,
            value_fn=lambda v: v == "On",
            write_fn=_enabled_write("x.com.samsung.da.autoSoftenerEnabled"),
        ),
        # Washer/dryer combo units carry a dryLevel field on the wash
        # resource itself (issue #22). Self-gates off on plain washers,
        # which never report supportedDryLevel.
        SelectDesc(
            key="dry_level",
            field="x.com.samsung.da.dryLevel",
            icon="mdi:tumble-dryer",
            entity_category="config",
            translation_key="washer_dry_level",
            options_field="x.com.samsung.da.supportedDryLevel",
            exists_fn=lambda rep, resources: bool(rep.get("x.com.samsung.da.supportedDryLevel")),
            write_fn=lambda p, rep, href=None: (
                ["washer", "vs", "0"],
                {"x.com.samsung.da.dryLevel": p},
            ),
        ),
    ),
)

# /course/vs/0 -- the cycle select is the shared laundry.cycle_select; the
# drum-clean and dispenser-dosing entities below are washer-specific reads
# off the same options array.

# Drum Clean+ maintenance tracking (issue #9): drum_clean_cycles_remaining/
# drum_clean_last_cleaned live in laundry.py, shared with dryer.py (issue
# #258) since both families report identical DrumCleanProposal_/
# WashingTimes_/DrumCleanLog_ tokens on the same options[] array.


# Detergent/softener auto-dispense dosing, from the same options[] array
# (issue #9). '<Prefix>LevelCtrl_<code>' is the selected dose quantity;
# '<Prefix>Level2Ctrl_<code>' is a second dial (water hardness for
# detergent, concentration for softener), matching the app's two-field
# dispenser screens. 'Supported<Prefix>Ctrl_<hexpairs>' lists the valid raw
# codes, same hex-pair shape as EditCourseList. '<Prefix>Alarm_<On/Off>' is
# a low-reservoir warning flag.
#
# Label mapping (translations/en.json's {detergent,softener}_quantity /
# detergent_water_hardness / softener_concentration) is an assumed reading
# of the single issue #9 dump + screenshots, cross-checked against the
# selected value on both dispensers, not independently verified per code --
# revisit if a second device's dump contradicts it.
def _supported_level_options(resources, prefix):
    rep = resources.get("/course/vs/0") or {}
    raw = option_value(rep.get("x.com.samsung.da.options"), f"Supported{prefix}")
    return hex_pairs(raw) if raw else []


def _level_options(prefix):
    return lambda resources: _supported_level_options(resources, prefix)


def _dosing_level(prefix):
    """Current dose code, normalized to the `Supported<prefix>` code
    format. The device reports the selected level as `<prefix>_<code>`
    un-padded (e.g. '3'), but the select's own options come from
    `Supported<prefix>_<hexpairs>` as zero-padded hex pairs (e.g. '03').
    Left as '3', the value sits outside the select's own option list and
    HA renders it 'unknown' (issue #9) -- resolve it to the matching
    zero-padded code instead."""

    def fn(rep):
        opts = rep.get("x.com.samsung.da.options")
        raw = option_value(opts, prefix)
        if raw is None:
            return None
        supported_raw = option_value(opts, f"Supported{prefix}")
        try:
            target = int(raw, 16)
        except (TypeError, ValueError):
            return raw
        for code in hex_pairs(supported_raw) if supported_raw else []:
            try:
                if int(code, 16) == target:
                    return code
            except (TypeError, ValueError):
                continue
        return raw

    return fn


def _level_write(prefix):
    def write(p, rep, href=None):
        if not rep.get("x.com.samsung.da.options"):
            return None
        # `p` is the zero-padded supported code (e.g. '03'); the device
        # stores it un-padded (e.g. '3'), matching how it's reported.
        try:
            native = format(int(p, 16), "X")
        except (TypeError, ValueError):
            native = p
        return ["course", "vs", "0"], {
            "x.com.samsung.da.options": option_write(prefix, native),
        }

    return write


def _dosing_low(prefix):
    return lambda rep: (
        option_value(rep.get("x.com.samsung.da.options"), prefix) not in (None, "Off")
    )


# Bubble soak / pre-wash / intensive-wash toggles, from the same options[]
# array (issue #22 follow-up). Each rides as a plain '<Prefix>_On'/'_Off'
# token, confirmed against a dump taken with Bubble Soak switched on in the
# app -- the same shape as AiOption/KidsLockBypass in this array.
#
# Each also has a hex-pair availability field positional with
# editCourseList (BubbleSoakSet, PreWashAvailableSet,
# IntensiveAvailableSet): on the reporter's dump 'F0' at a course's
# position matched the app enabling the control there, '00' matched it
# grayed out. exists_fn only runs once at setup, so it can't do this
# per-course check -- validate_fn runs on every write attempt instead,
# rejecting an on-write for a course whose byte isn't 'F0' with a
# user-facing error rather than silently no-opping. The read/write/
# presence machinery is laundry.bool_option_switch, shared with
# dishwasher's storm-wash/auto-release-dry toggles; only this per-course
# gating is washer-only.
def _bool_option_switch(key, icon, prefix, availability_field):
    def validate(p, rep, resources):
        """Reject turning on when the selected course's byte in
        `availability_field` isn't 'F0'. Turning off is never blocked.
        Falls back to allowing the write whenever the availability data
        can't be resolved (unrecognized course, missing/mismatched-length
        bitmap) -- a false rejection is worse than an occasional no-op."""
        if p != "On":
            return None
        opts = rep.get("x.com.samsung.da.options") or []
        current = option_value(opts, "Course")
        courses = cycle_options(resources)
        if not current or current not in courses:
            return None
        raw = option_value(opts, availability_field)
        if raw is None:
            return None
        pairs = hex_pairs(raw)
        if len(pairs) != len(courses):
            return None
        if pairs[courses.index(current)] != "F0":
            return f"{key}_unavailable_for_cycle"
        return None

    return bool_option_switch(
        key, icon, prefix, entity_category="config", gate_on_presence=True, validate_fn=validate
    )


# AddWash -- the little door for adding a forgotten sock mid-cycle -- rides
# three independent tokens on the same options[] array:
#
#   AddWashSet_<0-7>         the alarm setting, and the only writable one:
#                            a 3-bit mask over the moments it fires, bit 0
#                            rinse, bit 1 final rinse, bit 2 spin.
#   AddWashAvailable_<0-7>   the same three bits, but what the running
#                            course still permits.
#   AddWashIndicator_On/Off  the panel lamp: laundry may go in right now.
#
# Bit order confirmed by watching a WW6500 run a cycle: AddWashAvailable
# shed one bit as each moment passed (7 through Rinse, then 6, 4, and 0 as
# Spin began) and reset to 7 at the end, while the lamp tracked the phase
# with the alarm switched off throughout.


def _add_wash_mask(rep, prefix):
    """One of the 3-bit AddWash masks, or None when its token is absent,
    malformed, or outside 0-7. Never 0 for a missing token: 0 is a real
    value, and a mask this model can't represent is a wrong model rather
    than something to write back."""
    raw = option_value(rep.get("x.com.samsung.da.options"), prefix)
    try:
        mask = int(raw)
    except (TypeError, ValueError):
        return None
    return mask if 0 <= mask <= 0b111 else None


def _add_wash_any(prefix):
    """Whether any of the three moments is set in `prefix`'s mask."""

    def read(rep):
        mask = _add_wash_mask(rep, prefix)
        return None if mask is None else mask != 0

    return read


def _add_wash_set_write(mask):
    return ["course", "vs", "0"], {
        "x.com.samsung.da.options": option_write("AddWashSet", str(mask)),
    }


def _add_wash_alarm_write(p, rep, href=None):
    # Gated on the mask being readable, like the per-moment writes: a device
    # reporting a wider mask than these three bits would otherwise have it
    # truncated to 7 here, silently dropping a moment it supports.
    mask = _add_wash_mask(rep, "AddWashSet")
    if p not in ("On", "Off") or mask is None:
        return None
    if p == "On" and mask:
        # Already on, so "on" is a no-op rather than a rewrite to 7. Home
        # Assistant calls turn_on regardless of current state, so an
        # automation asserting the alarm on over a rinse-only mask would
        # otherwise widen it to all three moments with no state change on
        # this switch to point at. Distinct from the off-then-on case in
        # _add_wash_bit_switch, where there is no subset left to keep.
        return None
    return _add_wash_set_write(0b111 if p == "On" else 0)


def _add_wash_bit_switch(key, icon, bit):
    """One moment the alarm fires at, as its own bit of the mask.

    The mask is the only state, so switching the last moment off lands on 0
    and takes the alarm with it, and switching one on from 0 turns the alarm
    back on. The corollary is that switching the master off and on again
    writes 7, resetting a rinse-only selection to all three moments -- the
    appliance remembers no previous subset either, so there is nothing to
    restore.
    """

    def read(rep):
        mask = _add_wash_mask(rep, "AddWashSet")
        return None if mask is None else bool(mask >> bit & 1)

    def write(p, rep, href=None):
        mask = _add_wash_mask(rep, "AddWashSet")
        if p not in ("On", "Off") or mask is None:
            return None
        return _add_wash_set_write(mask | 1 << bit if p == "On" else mask & ~(1 << bit))

    return SwitchDesc(
        key=key,
        icon=icon,
        entity_category="config",
        exists_fn=bool_option_exists("AddWashSet"),
        rep_fn=read,
        write_fn=write,
    )


def _add_wash_indicator(rep):
    raw = option_value(rep.get("x.com.samsung.da.options"), "AddWashIndicator")
    return raw.lower() == "on" if isinstance(raw, str) else None


WASHER_COURSE = Capability(
    href="/course/vs/0",
    entities=(
        cycle_select(
            translation_key="washer_cycle",
            icon="mdi:washing-machine",
            table_href="/st/washercourse/vs/0",
            display_fn=washer_cycle_fallback,
        ),
        SensorDesc(
            key="drum_clean_cycles_remaining",
            unit="cycles",
            icon="mdi:washing-machine-alert",
            state_class="measurement",
            exists_fn=lambda rep, resources: drum_clean_cycles_remaining(rep) is not None,
            rep_fn=drum_clean_cycles_remaining,
        ),
        SensorDesc(
            key="drum_clean_last_cleaned",
            device_class="timestamp",
            icon="mdi:calendar-clock",
            entity_category="diagnostic",
            exists_fn=lambda rep, resources: drum_clean_last_cleaned(rep) is not None,
            rep_fn=drum_clean_last_cleaned,
        ),
        SelectDesc(
            key="detergent_quantity",
            icon="mdi:cup-water",
            translation_key="detergent_quantity",
            entity_category="config",
            options=_level_options("DetergentLevelCtrl"),
            exists_fn=lambda rep, resources: bool(_level_options("DetergentLevelCtrl")(resources)),
            rep_fn=_dosing_level("DetergentLevelCtrl"),
            write_fn=_level_write("DetergentLevelCtrl"),
        ),
        SelectDesc(
            key="detergent_water_hardness",
            icon="mdi:water-opacity",
            translation_key="detergent_water_hardness",
            entity_category="config",
            options=_level_options("DetergentLevel2Ctrl"),
            exists_fn=lambda rep, resources: bool(_level_options("DetergentLevel2Ctrl")(resources)),
            rep_fn=_dosing_level("DetergentLevel2Ctrl"),
            write_fn=_level_write("DetergentLevel2Ctrl"),
        ),
        SelectDesc(
            key="softener_quantity",
            icon="mdi:flask-outline",
            translation_key="softener_quantity",
            entity_category="config",
            options=_level_options("SoftenerLevelCtrl"),
            exists_fn=lambda rep, resources: bool(_level_options("SoftenerLevelCtrl")(resources)),
            rep_fn=_dosing_level("SoftenerLevelCtrl"),
            write_fn=_level_write("SoftenerLevelCtrl"),
        ),
        SelectDesc(
            key="softener_concentration",
            icon="mdi:flask-plus-outline",
            translation_key="softener_concentration",
            entity_category="config",
            options=_level_options("SoftenerLevel2Ctrl"),
            exists_fn=lambda rep, resources: bool(_level_options("SoftenerLevel2Ctrl")(resources)),
            rep_fn=_dosing_level("SoftenerLevel2Ctrl"),
            write_fn=_level_write("SoftenerLevel2Ctrl"),
        ),
        BinarySensorDesc(
            key="detergent_low",
            device_class="problem",
            icon="mdi:alert-circle-outline",
            exists_fn=bool_option_exists("DetergentAlarm"),
            rep_fn=_dosing_low("DetergentAlarm"),
        ),
        BinarySensorDesc(
            key="softener_low",
            device_class="problem",
            icon="mdi:alert-circle-outline",
            exists_fn=bool_option_exists("SoftenerAlarm"),
            rep_fn=_dosing_low("SoftenerAlarm"),
        ),
        _bool_option_switch("bubble_soak", "mdi:chart-bubble", "BubbleSoak", "BubbleSoakSet"),
        _bool_option_switch(
            "pre_wash", "mdi:washing-machine", "PreWashSetting", "PreWashAvailableSet"
        ),
        _bool_option_switch(
            "intensive", "mdi:washing-machine", "IntensiveSetting", "IntensiveAvailableSet"
        ),
        SwitchDesc(
            key="add_wash_alarm",
            icon="mdi:bell-ring",
            entity_category="config",
            exists_fn=bool_option_exists("AddWashSet"),
            rep_fn=_add_wash_any("AddWashSet"),
            write_fn=_add_wash_alarm_write,
        ),
        _add_wash_bit_switch("add_wash_alarm_rinse", "mdi:water", 0),
        _add_wash_bit_switch("add_wash_alarm_final_rinse", "mdi:water-check", 1),
        _add_wash_bit_switch("add_wash_alarm_spin", "mdi:sync", 2),
        # On at rest: an idle washer reports AddWashAvailable_7 and the mask
        # only empties as the cycle consumes each moment. This says the cycle
        # permits AddWash, not that laundry can go in now -- that is
        # add_wash_indicator.
        BinarySensorDesc(
            key="add_wash_available",
            icon="mdi:tshirt-crew-outline",
            entity_category="diagnostic",
            exists_fn=bool_option_exists("AddWashAvailable"),
            rep_fn=_add_wash_any("AddWashAvailable"),
        ),
        BinarySensorDesc(
            key="add_wash_indicator",
            icon="mdi:door-open",
            exists_fn=bool_option_exists("AddWashIndicator"),
            rep_fn=_add_wash_indicator,
        ),
    ),
)

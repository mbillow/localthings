"""Capabilities shared across the laundry family (washer, dryer, dishwasher).

Anything here is bound by more than one laundry registry, so it lives in one
place instead of being copied per family. Device-type-specific controls (wash
temperature, dry level, dishwasher storm-wash, etc.) stay in washer.py /
dryer.py / dishwasher.py; only the genuinely shared laundry surface is here.

Generic OCF controls that aren't laundry-specific (power, kids-lock, remote
control, energy meter) live in common.py, not here.

Resource hrefs seen across laundry dumps:
  /doorled/light/vs/0   -> DOOR_LED (door LED brightness / night light)
  /settings/sound/*/vs/0-> SOUND_MODE / SOUND_VOLUME
  /buzzersound/vs/0     -> BUZZER_SOUND (buzzer + optional finish chime)
  /course/vs/0          -> the cycle select + per-family course options
  /wm/editcourse/vs/0   -> live editCourseList that drives the cycle options
  /wm/jobbeginingstatus/vs/0 -> JOB_BEGINNING_STATUS

Door-LED keys use NO `x.com.samsung.da.` prefix -- `setBrightness` /
`setNightLight` -- preserved exactly as they appear in the OCF resource rep.
"""

from datetime import UTC, datetime
from datetime import time as dt_time

from ... import cloudcourse
from ...catalog import has_entity_translation
from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SensorDesc, SwitchDesc, TimeDesc
from .common import hex_pairs, option_value

_LED_LEVELS = ("Low", "High")
_SOUND_MODES = ("voice", "tone", "mute")


def _led_brightness_write(p, rep, href=None):
    if p not in _LED_LEVELS:
        return None
    return ["doorled", "light", "vs", "0"], {"setBrightness": p}


def _led_night_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["doorled", "light", "vs", "0"], {"setNightLight": p}


def _parse_hm(v):
    if not v:
        return None
    try:
        h, m = v.split(":")
        return dt_time(int(h), int(m))
    except Exception:
        return None


def _sound_mode_write(p, rep, href=None):
    if p not in _SOUND_MODES:
        return None
    return ["settings", "sound", "mode", "vs", "0"], {"mode": p}


DOOR_LED = Capability(
    href="/doorled/light/vs/0",
    entities=(
        SelectDesc(
            key="led_brightness",
            field="setBrightness",
            icon="mdi:brightness-6",
            entity_category="config",
            options=_LED_LEVELS,
            write_fn=_led_brightness_write,
        ),
        SwitchDesc(
            key="led_night_light",
            field="setNightLight",
            icon="mdi:weather-night",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=_led_night_write,
        ),
        SelectDesc(
            key="led_night_brightness",
            field="setNightLightBrightness",
            icon="mdi:brightness-4",
            entity_category="config",
            options=_LED_LEVELS,
            write_fn=lambda p, rep, href=None: (
                ["doorled", "light", "vs", "0"],
                {"setNightLightBrightness": p},
            ),
        ),
        TimeDesc(
            key="led_night_start",
            field="setNightLightTimeStart",
            icon="mdi:clock-start",
            entity_category="config",
            value_fn=_parse_hm,
            write_fn=lambda p, rep, href=None: (
                ["doorled", "light", "vs", "0"],
                {"setNightLightTimeStart": f"{p.hour:02d}:{p.minute:02d}"},
            ),
        ),
        TimeDesc(
            key="led_night_end",
            field="setNightLightTimeEnd",
            icon="mdi:clock-end",
            entity_category="config",
            value_fn=_parse_hm,
            write_fn=lambda p, rep, href=None: (
                ["doorled", "light", "vs", "0"],
                {"setNightLightTimeEnd": f"{p.hour:02d}:{p.minute:02d}"},
            ),
        ),
    ),
)

SOUND_MODE = Capability(
    href="/settings/sound/mode/vs/0",
    entities=(
        SelectDesc(
            key="sound_mode",
            field="mode",
            icon="mdi:volume-high",
            entity_category="config",
            options=_SOUND_MODES,
            write_fn=_sound_mode_write,
        ),
    ),
)

SOUND_VOLUME = Capability(
    href="/settings/sound/volume/vs/0",
    entities=(
        NumberDesc(
            key="sound_volume",
            field="level",
            icon="mdi:volume-medium",
            entity_category="config",
            native_min=0,
            native_max=15,
            step=5,
            value_fn=lambda v: int(v) if v is not None else None,
            write_fn=lambda p, rep, href=None: (
                ["settings", "sound", "volume", "vs", "0"],
                {"level": str(int(p))},
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# /buzzersound/vs/0 -- buzzer volume and (on some units) a separate finish
# chime. Fields have no 'x.com.samsung.da.' prefix in this resource. Seen on
# washers and DA_WM_TP1 dryers; the dryer dump carries only setBuzzerSound
# (no supportedFinishSound), so finish_sound self-gates off there.
# ---------------------------------------------------------------------------

BUZZER_SOUND = Capability(
    href="/buzzersound/vs/0",
    entities=(
        SelectDesc(
            key="buzzer_sound",
            field="setBuzzerSound",
            icon="mdi:volume-high",
            entity_category="config",
            options_field="supportedBuzzerSound",
            write_fn=lambda p, rep, href=None: (["buzzersound", "vs", "0"], {"setBuzzerSound": p}),
        ),
        SelectDesc(
            key="finish_sound",
            field="setFinishSound",
            icon="mdi:bell-ring",
            entity_category="config",
            exists_fn=lambda rep, resources: "supportedFinishSound" in rep,
            options_field="supportedFinishSound",
            write_fn=lambda p, rep, href=None: (["buzzersound", "vs", "0"], {"setFinishSound": p}),
        ),
    ),
)

# Cycle selection over /course/vs/0.
#
# The selected course and every other user-tunable option ride in the
# x.com.samsung.da.options array as `<Prefix>_<value>` tokens. Confirmed on
# real hardware (issue #54): a write only needs to carry the one changed
# token -- the device matches by prefix, evicts the stale token, and merges
# the result itself (see option_write). The set of selectable courses is
# read live from editCourseList on /wm/editcourse/vs/0 (cycle_options), not
# hardcoded. Course codes are uppercase hex; display names live in
# translations under entity.select.<translation_key>.state.<id lowercased>.
#
# Some boards populate /wm/editcourse/vs/0 without ever filling in
# editCourseList itself (issue #1) -- cycle_options() falls back to
# deriving the list from /course/vs/0's own supportedOptions in that case;
# see _course_codes_from_supported_options.
#
# Shared verbatim by washer, dishwasher, and dryer -- all DA_WM_-family
# boards expose the same /course/vs/0 options contract.


def parse_edit_course_list(raw):
    """'EditCourseList_1C1D21...' -> ['1C', '1D', '21', ...]."""
    if not isinstance(raw, str) or "_" not in raw:
        return []
    return hex_pairs(raw.split("_", 1)[1])


def cycle_options(resources):
    rep = resources.get("/wm/editcourse/vs/0") or {}
    codes = parse_edit_course_list(rep.get("x.com.samsung.da.editCourseList"))
    if codes:
        return codes
    return _course_codes_from_supported_options(resources.get("/course/vs/0") or {})


# Drum Clean+ maintenance tracking, from the same options[] array as the
# selected course -- shared by washer.py (issue #9) and dryer.py (issue
# #258), identical DrumCleanProposal_/WashingTimes_/DrumCleanLog_ tokens.
# DrumCleanProposal_<N> is the cycle interval between recommended cleans;
# WashingTimes_<N> is the count since the last one -- their difference is
# the "N cycles until due" figure the app shows (verified: 40 - 3 == 37,
# matching a live app screenshot).
def drum_clean_cycles_remaining(rep):
    opts = rep.get("x.com.samsung.da.options") or []
    proposal = option_value(opts, "DrumCleanProposal")
    washed = option_value(opts, "WashingTimes")
    if proposal is None or washed is None:
        return None
    try:
        return max(int(proposal) - int(washed), 0)
    except ValueError:
        return None


# DrumCleanLog_ is the clean-history field: a washer reports one bare ISO
# datetime (the last clean); a dryer (issue #258) instead reports a
# '|'-joined history of every past clean in increasing order. Splitting on
# '|' and taking the last element handles both shapes identically. No
# timezone accompanies either shape, so it's treated as UTC.
def drum_clean_last_cleaned(rep):
    raw = option_value(rep.get("x.com.samsung.da.options"), "DrumCleanLog")
    if not raw:
        return None
    last = raw.rsplit("|", 1)[-1]
    try:
        return datetime.fromisoformat(last).replace(tzinfo=UTC)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# supportedOptions -- what each course permits.
#
# A 1-nibble header followed by one fixed-width record per selectable
# course: the course's own hex code, then zero or more 2-byte groups.
#
#   <hdr:1 nibble> ( <course:1> ( <kind:nibble><default:nibble> <mask:1> )* )*
#
# The header is the group count, so it states the record width: 1 + 2*hdr
# bytes. True on all 17 dumps here, across six distinct widths, and it
# reproduces the codes the old smallest-that-parses scan derived on every
# one of them -- see _course_records, which keeps that scan as a fallback.
#
# The mask indexes that option's own supported<Option> list, and so does
# the default nibble -- but into the list, not into the mask: a dishwasher
# reports default 0 with a mask allowing only index 1. It is one byte, so
# it cannot address past index 7: four boards carry an 11- or 13-entry
# supportedDryTime, and none of them carries a group for it.
#
# Five kinds are named: 0x8 water temperature, 0x9 rinse and 0xA spin from a
# WW6500 panel reading plus a list-length argument (the reading alone cannot
# separate rinse from spin), 0xD dry from a DV5000T's, and 0xE dry time from
# the DV6800N, where it complements 0xD course for course. The rest stay
# unnamed, 0xB included -- but 0xB, not 0xD, is the dry dial on the WW6600R
# combo, which carries no 0xD at all, so a gate keyed on 0xD alone silently
# no-ops there. Evidence for all of it in
# docs/investigations/course-option-groups.md.
#
# A kind is not an entity name: dishwashers carry 0xD with no
# supportedDryLevel at all (their dry setting is heated_dry), so callers key
# the mask against their own list rather than assuming what it counts.
#
# Two things a caller cannot infer from the bytes:
#
#   - A mask is what the device advertises, not what it enforces. A WW6500
#     takes only 2-5 rinses on Baby Care -- on its own panel, not just in
#     SmartThings -- where the mask allows all six. Gating on one offers at
#     most a little too much, which the appliance then refuses.
#   - An empty mask is NOT automatically "nothing is selectable". On a
#     dryer's Quick Dry it is, but a washer's cloud Download slot reports
#     empty masks for every kind while still holding live values, because
#     the downloaded program supplies its own. That call is the caller's.
# ---------------------------------------------------------------------------

OPTION_KIND_WATER_TEMPERATURE = 0x8
OPTION_KIND_RINSE = 0x9
OPTION_KIND_SPIN = 0xA
OPTION_KIND_DRY = 0xD

# Named on one board, but not on "it decodes against supportedDryTime"
# alone: on the DV6800N -- the only dump carrying both -- every record holds
# a 0xD and a 0xE group, no course carries values for both, and the only
# one carrying values for neither is Quick Dry, which takes no dry setting
# at all. A kind that was not the dry dial's timed counterpart would not
# complement it that precisely. See the investigation doc; a second board
# carrying 0xE would settle it outright.
OPTION_KIND_DRY_TIME = 0xE

# A mask is one byte, so it can only speak about the first eight entries of
# the supported<Option> list it indexes. Four boards carry an 11- or
# 13-entry supportedDryTime and no group for it, so their dry time is not
# narrowed at all; where a board carries both, this keeps the unaddressable
# tail rather than reading its absence from the mask as a refusal.
MASK_ADDRESSABLE = 8


def _on_download_course(resources):
    """True when the live course is this device's confirmed Download slot.

    Read straight from the course rep rather than through cloud_current():
    that one additionally requires a *named* program to be loaded, and an
    unnamed Download selection is just as much "the mask is not describing
    this course".
    """
    rep = resources.get(cloudcourse.COURSE_HREF) or {}
    download = (rep.get(cloudcourse.FIELD) or {}).get("download_course")
    return (
        bool(download) and option_value(rep.get("x.com.samsung.da.options"), "Course") == download
    )


def _course_records(course_rep, must_cover=None):
    """{course code: record hex} from supportedOptions, or {} if unreadable.

    The header states the width (1 + 2*hdr bytes), so that split is tried
    first and taken whenever it satisfies the guards below. The
    smallest-that-parses scan those guards were written for stays as the
    fallback: it is what every course list shipped so far was derived from,
    and the header rule reproduces it exactly on all 17 dumps, so nothing
    rests on the header being right about a board none of us has seen.

    `must_cover` is a further check, not a search: the split's codes must
    include every code given. Callers that can see a live editCourseList
    pass it. An unsatisfiable one no longer means "say nothing" -- a device
    that lists a course supportedOptions does not carry (a personal or
    cloud slot, say) would otherwise switch the decoder off wholesale, and
    silently, which is the failure mode this module is most anxious about.
    A covering split wins; failing that, the smallest passing one does.
    """
    raw = course_rep.get("x.com.samsung.da.supportedOptions")
    hexstr = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(hexstr, str) or len(hexstr) < 3:
        return {}
    body = hexstr[1:]
    if len(body) % 2:
        return {}
    total_bytes = len(body) // 2
    current = option_value(course_rep.get("x.com.samsung.da.options"), "Course")

    def split(k):
        """Records at a width of k bytes, or None if k can't be the width."""
        if k < 1 or total_bytes % k:
            return None
        n = total_bytes // k
        # Two records minimum: k == total_bytes satisfies every guard below
        # trivially, so a table of one is indistinguishable from garbage.
        if n < 2:
            return None
        records = [body[i * k * 2 : (i + 1) * k * 2] for i in range(n)]
        firsts = [r[:2] for r in records]
        if len(set(firsts)) != n:
            return None
        if current is not None and current not in firsts:
            return None
        return dict(zip(firsts, records, strict=True))

    try:
        stated = split(1 + 2 * int(hexstr[0], 16))
    except ValueError:
        stated = None  # header isn't hex, so it states nothing
    if stated is not None and (not must_cover or must_cover <= set(stated)):
        return stated

    fallback = None
    for k in range(1, total_bytes + 1):
        records = split(k)
        if records is None:
            continue
        if must_cover and not must_cover <= set(records):
            fallback = fallback or records
            continue
        return records
    return fallback or {}


def course_option_mask(resources, kind, course=None):
    """(default index, allowed indices) for `kind` on the selected course.

    None when this device says nothing usable -- no supportedOptions, an
    unrecognized course, or no group of that kind on the record. Callers
    treat that as "no opinion" rather than "nothing allowed": several
    boards carry supported<Option> lists with no supportedOptions groups at
    all, and refusing everything there would be worse than not gating.

    The default index is into the same supported<Option> list as the
    allowed indices, but it is NOT necessarily one of them -- a dishwasher
    reports default 0 alongside a mask allowing only index 1. Callers that
    want a fallback value have to decide for themselves whether an
    out-of-set default is usable.

    Where the device also publishes an editCourseList, it is handed to
    _course_records as a check on the split -- free here, and worth having
    where the header and the scan could disagree.

    `course` is matched case-insensitively. Device-side codes are uppercase
    hex, but select.py lowercases an entity's options for the translation
    catalog, so a caller reaching for what the entity displays rather than
    the raw Course_ token would otherwise get a silent "no opinion".
    """
    course_rep = resources.get("/course/vs/0") or {}
    edit_list = parse_edit_course_list(
        (resources.get("/wm/editcourse/vs/0") or {}).get("x.com.samsung.da.editCourseList")
    )
    records = _course_records(course_rep, must_cover=set(edit_list))
    if not records:
        return None
    if course is None:
        course = option_value(course_rep.get("x.com.samsung.da.options"), "Course")
    if not isinstance(course, str):
        return None
    record = {code.upper(): r for code, r in records.items()}.get(course.upper())
    if record is None:
        return None
    for i in range(2, len(record), 4):
        group = record[i : i + 4]
        if len(group) < 4:
            break
        try:
            head = int(group[:2], 16)
            mask = int(group[2:], 16)
        except ValueError:
            # Not hex after all, so this split is not a record table --
            # the width guards alone can't tell. Say nothing rather than
            # raise into whatever entity asked.
            return None
        if head >> 4 != kind:
            continue
        return head & 0xF, [bit for bit in range(8) if mask >> bit & 1]
    return None


def course_narrowed_options(kind, field, options_field, href="/washer/vs/0"):
    """Build a SelectDesc `options` callable that narrows a supported<Option>
    list to what the *selected course* actually accepts.

    Without this, a dryer offers every dry level its board supports on every
    course, and the ones the running course rejects are simply ignored by the
    appliance -- no error, no state change, nothing the user can see. Handing
    the entity a narrowed list turns that silent no-op into a visible
    ServiceValidationError from Home Assistant's own option check.

    `kind` is one of the OPTION_KIND_* nibbles above; `field` and
    `options_field` are this entity's live value and its full supported list
    on `href`.

    Three rules, none of which the mask decides on its own:

      - **Never blank the entity.** The result is the decoded set *union the
        live value*: `options` and `current_option` are computed independently
        by select.py, and HA's SelectEntity.state returns None when the
        current option isn't in the list -- so a course change landing before
        the board updates its dryLevel would otherwise read as `unknown`.
      - **Never narrow to empty.** An empty `_raw_options()` is the
        "unpopulated" contract that pairs with exists_fn suppression, and
        these descriptors deliberately have none, so an empty list means a
        live entity with an empty dropdown rather than no entity. No opinion
        from the decoder (`None`) falls back to the full supported list; a
        course that genuinely allows nothing falls back to just the live
        value.
      - **The supported list sets the order**, not the mask, so the dropdown
        doesn't reshuffle as the course changes. A live value the supported
        list omits is appended rather than dropped.

    Two of the module comment's warnings are this caller's to answer, and
    both are answered by offering *more* rather than less -- the mask is
    what the device advertises, not what it enforces, so over-offering costs
    at most a write the appliance refuses, while under-offering makes a
    level the user really can select unreachable through Home Assistant:

      - **An empty mask on the Download course is not "nothing selectable".**
        A cloud slot reports empty masks for every kind while its values stay
        live, because the downloaded program supplies its own. Only a local
        course's empty mask (a dryer's Quick Dry) means what it says.
      - **A one-byte mask cannot address past index 7.** Where the supported
        list is longer, the entries beyond that are unaddressable rather than
        disallowed, so they are kept.
    """

    def _options(resources):
        rep = resources.get(href) or {}
        supported = list(rep.get(options_field) or [])
        live = rep.get(field)
        if not supported:
            # Unpopulated rep: keep the existing contract rather than
            # inventing a single-entry list out of a live value.
            return []

        mask = course_option_mask(resources, kind)
        if mask is None:
            return supported  # no opinion -- see course_option_mask
        _default, allowed = mask

        if not allowed and _on_download_course(resources):
            # A cloud slot's empty mask is not this course refusing every
            # value; the downloaded program carries its own, and the board
            # keeps taking writes. Treat it as no opinion.
            return supported

        # The mask indexes the supported list; an index past its end is the
        # device describing a longer list than it published here, which says
        # nothing about the entries that do exist. Past MASK_ADDRESSABLE the
        # reverse holds: the mask *cannot* reach those entries, so their
        # absence from it is not a refusal either.
        keep = {supported[i] for i in allowed if i < len(supported)}
        keep.update(supported[MASK_ADDRESSABLE:])
        if isinstance(live, str):
            keep.add(live)

        narrowed = [o for o in supported if o in keep]
        if isinstance(live, str) and live not in narrowed:
            narrowed.append(live)
        return narrowed or supported

    return _options


def _course_codes_from_supported_options(course_rep):
    """Fallback for an empty/missing editCourseList: derive the selectable
    course list from /course/vs/0's own supportedOptions instead (issue #1:
    some boards populate /wm/editcourse/vs/0 but never fill in
    editCourseList itself).

    supportedOptions is a 1-hex-nibble header followed by one fixed-width
    record per selectable course, self-indexed rather than positional --
    the first byte of every record is that course's own hex code.
    Confirmed against six independent real-world dumps: every one divides
    evenly into `header + N * K bytes` with fully unique first bytes across
    all N records, at the record's true byte width.

    K is the width the header states, checked by two conservative guards
    rather than trusted outright: the derived codes must all be distinct,
    and must include whatever course is currently selected. Where the
    header doesn't hold up, the smallest passing K wins instead -- the
    heuristic this fallback shipped with, kept because every course list
    derived so far came out of it. If nothing passes, this returns [].

    The record payload behind those first bytes is decoded by
    course_option_mask above; both share _course_records, so the split
    they see can never drift apart.
    """
    return list(_course_records(course_rep))


def option_tokens(*pairs):
    """[(prefix, value), ...] -> ['<prefix>_<value>', ...] -- the general
    form of option_write, for the one write that needs two tokens to land in
    the same options[] array together (see cycle_write's cloud branch)."""
    return [f"{prefix}_{value}" for prefix, value in pairs]


def option_write(prefix, new_value):
    """A one-token x.com.samsung.da.options write -- see the module comment
    above for why this doesn't read/rewrite the whole array."""
    return option_tokens((prefix, new_value))


# ---------------------------------------------------------------------------
# Cloud "Download" programs, folded into this same cycle select (issue #342).
#
# A device that has downloaded programs advertises them on the same
# /course/vs/0 options array; cloudcourse.py owns the token shapes, the
# learned store, and the reasoning for all of it. Everything below is just
# how that store reaches the select: the coordinator merges it onto this
# href's rep under cloudcourse.FIELD, so the option list, current value,
# label, and write path each read it from the rep or snapshot they already
# receive.
#
# They ride in the cycle select rather than a select of their own because
# that is what they are to a user -- on the appliance's own controls,
# "Download" occupies one position among the ordinary courses, and picking a
# downloaded program is picking a cycle. Their raw values are namespaced
# ('cloud:<slot>') so they can never be confused with, or collide with, a
# two-hex-char local course code.
#
# Bound by whichever families declare it. Washers are where this was worked
# out, but a DW5000C dishwasher advertises the same token (see
# cloudcourse.py), so nothing below is washer-specific.
#
# Confirmed on hardware before any of this was written (issue #342): writing
# the program token alone, while some other course is selected, is silently
# ignored -- the course token has to switch to Download in the *same* write.
# Hence the two-token write, the only one in this module.


def _cloud_state(rep):
    return rep.get(cloudcourse.FIELD) or {}


def cloud_options(rep):
    """Namespaced raw values for every named, learned cloud program."""
    return [
        f"{cloudcourse.RAW_PREFIX}{slot}" for slot in sorted(_cloud_state(rep).get("programs", {}))
    ]


def cloud_label(value, resources):
    """The user's own name for a 'cloud:<slot>' value.

    Cloud program names are user-supplied, never translated: the appliance
    reports only an opaque slot id, and inventing an English label for one
    is exactly what this module refuses to do for unrecognized local course
    codes (see washer_cycle_fallback).
    """
    if not isinstance(value, str) or not value.startswith(cloudcourse.RAW_PREFIX):
        return None
    slot = value[len(cloudcourse.RAW_PREFIX) :]
    rep = resources.get(cloudcourse.COURSE_HREF) or {}
    program = _cloud_state(rep).get("programs", {}).get(slot)
    return program["name"] if program else None


def cloud_current(rep):
    """'cloud:<slot>' when a named cloud program is the live selection.

    Gated on the course actually being this device's confirmed Download
    course: tokens in this array are replaced by prefix and never evicted, so
    a one-time program token outlives the run it belonged to and would
    otherwise report "Jeans" while an ordinary cotton cycle runs.
    """
    state = _cloud_state(rep)
    download = state.get("download_course")
    options = rep.get("x.com.samsung.da.options")
    if not download or option_value(options, "Course") != download:
        return None
    blob = option_value(options, cloudcourse.ONESHOT_PREFIX)
    slot = cloudcourse.slot_of(blob)
    if slot is None:
        # No one-time override loaded: the appliance falls back to whatever
        # the persisted default holds (confirmed with the issue #342
        # reporter -- leaving Download and returning to it re-selects the
        # saved program, not the last one-time one).
        slot = cloudcourse.slot_of(option_value(options, cloudcourse.DEFAULT_PREFIX))
    if slot is None or slot not in state.get("programs", {}):
        return None
    return f"{cloudcourse.RAW_PREFIX}{slot}"


def cycle_write(p, rep, href=None):
    if not rep.get("x.com.samsung.da.options"):
        return None
    if isinstance(p, str) and p.startswith(cloudcourse.RAW_PREFIX):
        return _cloud_cycle_write(p, rep)
    return ["course", "vs", "0"], {
        "x.com.samsung.da.options": option_write("Course", p),
    }


def _cloud_cycle_write(p, rep):
    state = _cloud_state(rep)
    download = state.get("download_course")
    program = state.get("programs", {}).get(p[len(cloudcourse.RAW_PREFIX) :])
    if not download or program is None:
        return None
    # Order matches what the appliance was confirmed to accept.
    return ["course", "vs", "0"], {
        "x.com.samsung.da.options": option_tokens(
            ("Course", download), (cloudcourse.ONESHOT_PREFIX, program["blob"])
        ),
    }


def personal_course_labels(resources, href="/wm/personalcourse/vs/0"):
    """Return device-provided personal course names keyed by course code.

    Populated entries use a small TLV payload. The leading field is
    ``01 <UTF-8-byte-length> <name>``; later fields contain a description and
    settings and are intentionally left uninterpreted. Empty slots are
    encoded as ``<code>_00``. Malformed or undecodable entries are ignored so
    opaque device data can never become a misleading label.
    """
    rep = resources.get(href) or {}
    labels = {}
    for entry in rep.get("x.com.samsung.da.courses") or []:
        if not isinstance(entry, str) or "_" not in entry:
            continue
        code, encoded = entry.split("_", 1)
        try:
            payload = bytes.fromhex(encoded)
        except ValueError:
            continue
        if len(payload) < 3 or payload[0] != 0x01:
            continue
        name_length = payload[1]
        if name_length == 0 or len(payload) < 2 + name_length:
            continue
        try:
            name = payload[2 : 2 + name_length].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if name.strip() and name.isprintable():
            labels[code.upper()] = name
    return labels


def washer_cycle_fallback(value, resources):
    """Label a personal washer course from its device-provided name.

    No fallback for an unrecognized standard code -- an invented English
    label would defeat translation (PR #251 review); the raw code displays
    instead, same as before this function existed.
    """
    if not isinstance(value, str):
        return None
    return personal_course_labels(resources).get(value.upper())


def _table_id(resources, table_href):
    rep = resources.get(table_href) or {}
    return rep.get("x.com.samsung.da.st.courseTable")


def cycle_select(*, translation_key, icon, table_href=None, display_fn=None):
    """A 'Cycle' select over /course/vs/0, labelled from `translation_key`.

    The option list, current value, and write path are shared across
    washer/dryer/dishwasher; only the translation is family/board-specific.

    table_href (washer/dryer only) suffixes translation_key with the
    device's own course-table id, read from /st/washercourse/vs/0 or
    /st/dryercourse/vs/0's courseTable (e.g. 'washer_cycle' + 'Table_02' ->
    'washer_cycle_table_02'). This matters because course codes are NOT
    guaranteed consistent across board generations sharing the same
    /course/vs/0 contract: washer_cycle_table_02 was confirmed against
    Table_02 devices, but FlexWash's older board reports Table_00, where
    the same hex code could mean a different course. An absent or
    unrecognized table id falls back to the name-only ``cycle`` key
    instead of borrowing a label from another board generation --
    translating a new table is a translations-only change.

    The raw course code remains writable regardless; its display uses
    display_fn when supplied, otherwise it remains raw. display_fn is an
    optional family-specific fallback for untranslated raw values --
    select.py applies it after catalog lookup to both state and options.

    Left at its default for dishwasher, which has no equivalent table-id
    resource and no evidence its codes vary by table the way washer/
    dryer's do.

    Any cloud "Download" programs the user has discovered and named join the
    same option list, after the local courses -- see the cloud section above.
    A device with none (or one whose owner hasn't named any yet) gets exactly
    the list it got before they existed.
    """
    key = translation_key
    if table_href is not None:

        def key(resources):
            table = _table_id(resources, table_href)
            if not isinstance(table, str) or not table:
                return "cycle"
            candidate = f"{translation_key}_{table.lower()}"
            return candidate if has_entity_translation("select", candidate) else "cycle"

    def options(resources):
        rep = resources.get(cloudcourse.COURSE_HREF) or {}
        # Local courses first: a user-supplied cloud name that happens to
        # match a translated course name resolves back to the real local
        # course on write, which is the safer of the two. The options flow
        # rejects such a name outright, so this is a backstop, not the fix.
        return [*cycle_options(resources), *cloud_options(rep)]

    def current(rep):
        return cloud_current(rep) or option_value(rep.get("x.com.samsung.da.options"), "Course")

    def label(value, resources):
        cloud = cloud_label(value, resources)
        if cloud is not None:
            return cloud
        return display_fn(value, resources) if display_fn is not None else None

    return SelectDesc(
        key="cycle",
        icon=icon,
        translation_key=key,
        options=options,
        exists_fn=lambda rep, resources: bool(options(resources)),
        rep_fn=current,
        display_fn=label,
        write_fn=cycle_write,
    )


# Plain boolean toggles over /course/vs/0's options[] array: a
# '<prefix>_On'/'<prefix>_Off' token, merged the same way as the 'Course'
# token above. Shared by washer (bubble soak, pre-wash, intensive -- issue
# #22) and dishwasher (storm wash, auto release dry), just with different
# prefixes and presence/validation needs on top.


def bool_option_write(prefix):
    def write(p, rep, href=None):
        if p not in ("On", "Off"):
            return None
        if not rep.get("x.com.samsung.da.options"):
            return None
        return ["course", "vs", "0"], {
            "x.com.samsung.da.options": option_write(prefix, p),
        }

    return write


def bool_option_value(prefix):
    return lambda rep: option_value(rep.get("x.com.samsung.da.options"), prefix) == "On"


def bool_option_exists(prefix):
    return lambda rep, resources: (
        option_value(rep.get("x.com.samsung.da.options"), prefix) is not None
    )


def bool_option_switch(
    key, icon, prefix, *, entity_category=None, gate_on_presence=False, validate_fn=None
):
    """A SwitchDesc over a '<prefix>_On'/'<prefix>_Off' options[] token.

    gate_on_presence self-gates the entity off on models that never report
    the token (washer's bubble soak/pre-wash/intensive); leave False for a
    toggle every device in the family reports (dishwasher's storm wash).
    validate_fn passes straight through to SwitchDesc for callers that need
    to reject a write against live state -- this factory has no opinion on
    it.
    """
    return SwitchDesc(
        key=key,
        icon=icon,
        entity_category=entity_category,
        exists_fn=bool_option_exists(prefix) if gate_on_presence else None,
        rep_fn=bool_option_value(prefix),
        write_fn=bool_option_write(prefix),
        validate_fn=validate_fn,
    )


# /wm/jobbeginingstatus/vs/0 -- the "why did the cycle not start" reason
# (e.g. door open, no water), x.com.samsung.da.currentStatus on every dump
# that populates it. An earlier dryer descriptor read
# x.com.samsung.da.jobBeginingStatus instead, which no dump ever carried,
# so the dryer sensor was always blank -- fixed by sharing this one reader.

JOB_BEGINNING_STATUS = Capability(
    href="/wm/jobbeginingstatus/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="job_beginning_status",
            field="x.com.samsung.da.currentStatus",
            entity_category="diagnostic",
        ),
    ),
)

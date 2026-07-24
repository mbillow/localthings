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
from datetime import time as dt_time

from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SensorDesc, SwitchDesc, TimeDesc

_LED_LEVELS = ('Low', 'High')
_SOUND_MODES = ('voice', 'tone', 'mute')


def _led_brightness_write(p, rep, href=None):
    if p not in _LED_LEVELS:
        return None
    return ['doorled', 'light', 'vs', '0'], {'setBrightness': p}


def _led_night_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['doorled', 'light', 'vs', '0'], {'setNightLight': p}


def _parse_hm(v):
    if not v:
        return None
    try:
        h, m = v.split(':')
        return dt_time(int(h), int(m))
    except Exception:
        return None


def _sound_mode_write(p, rep, href=None):
    if p not in _SOUND_MODES:
        return None
    return ['settings', 'sound', 'mode', 'vs', '0'], {'mode': p}


DOOR_LED = Capability(
    href='/doorled/light/vs/0',
    entities=(
        SelectDesc(key='led_brightness', field='setBrightness',
                   name='Door LED brightness', icon='mdi:brightness-6',
                   entity_category='config',
                   options=_LED_LEVELS, write_fn=_led_brightness_write),
        SwitchDesc(key='led_night_light', field='setNightLight',
                   name='Door LED night light', icon='mdi:weather-night',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_led_night_write),
        SelectDesc(key='led_night_brightness', field='setNightLightBrightness',
                   name='Door LED night brightness', icon='mdi:brightness-4',
                   entity_category='config',
                   options=_LED_LEVELS,
                   write_fn=lambda p, rep, href=None: (
                       ['doorled', 'light', 'vs', '0'],
                       {'setNightLightBrightness': p})),
        TimeDesc(key='led_night_start', field='setNightLightTimeStart',
                 name='Door LED night start', icon='mdi:clock-start',
                 entity_category='config',
                 value_fn=_parse_hm,
                 write_fn=lambda p, rep, href=None: (
                     ['doorled', 'light', 'vs', '0'],
                     {'setNightLightTimeStart': f'{p.hour:02d}:{p.minute:02d}'})),
        TimeDesc(key='led_night_end', field='setNightLightTimeEnd',
                 name='Door LED night end', icon='mdi:clock-end',
                 entity_category='config',
                 value_fn=_parse_hm,
                 write_fn=lambda p, rep, href=None: (
                     ['doorled', 'light', 'vs', '0'],
                     {'setNightLightTimeEnd': f'{p.hour:02d}:{p.minute:02d}'})),
    ),
)

SOUND_MODE = Capability(
    href='/settings/sound/mode/vs/0',
    entities=(
        SelectDesc(key='sound_mode', field='mode',
                   name='Sound mode', icon='mdi:volume-high',
                   entity_category='config',
                   options=_SOUND_MODES, write_fn=_sound_mode_write),
    ),
)

SOUND_VOLUME = Capability(
    href='/settings/sound/volume/vs/0',
    entities=(
        NumberDesc(key='sound_volume', field='level',
                   name='Sound volume', icon='mdi:volume-medium',
                   entity_category='config',
                   native_min=0, native_max=15, step=5,
                   value_fn=lambda v: int(v) if v is not None else None,
                   write_fn=lambda p, rep, href=None: (
                       ['settings', 'sound', 'volume', 'vs', '0'],
                       {'level': str(int(p))})),
    ),
)

# ---------------------------------------------------------------------------
# /buzzersound/vs/0 -- buzzer volume and (on some units) a separate finish
# chime. Fields have no 'x.com.samsung.da.' prefix in this resource. Seen on
# washers and DA_WM_TP1 dryers; the dryer dump carries only setBuzzerSound
# (no supportedFinishSound), so finish_sound self-gates off there.
# ---------------------------------------------------------------------------

BUZZER_SOUND = Capability(
    href='/buzzersound/vs/0',
    entities=(
        SelectDesc(key='buzzer_sound', field='setBuzzerSound',
                   name='Buzzer sound', icon='mdi:volume-high',
                   entity_category='config',
                   options_field='supportedBuzzerSound',
                   write_fn=lambda p, rep, href=None: (
                       ['buzzersound', 'vs', '0'], {'setBuzzerSound': p})),
        SelectDesc(key='finish_sound', field='setFinishSound',
                   name='Finish sound', icon='mdi:bell-ring',
                   entity_category='config',
                   exists_fn=lambda rep, resources: 'supportedFinishSound' in rep,
                   options_field='supportedFinishSound',
                   write_fn=lambda p, rep, href=None: (
                       ['buzzersound', 'vs', '0'], {'setFinishSound': p})),
    ),
)

# ---------------------------------------------------------------------------
# Cycle selection over /course/vs/0.
#
# The selected course and every other user-tunable option ride in the
# x.com.samsung.da.options array on /course/vs/0 as `<Prefix>_<value>` tokens;
# a write is a read-modify-write of that whole array (cycle_write). The set of
# *selectable* courses is not hardcoded -- it's read live from
# x.com.samsung.da.editCourseList on /wm/editcourse/vs/0 (cycle_options), so we
# never show a course a given model doesn't have or hide one it does. Course
# codes are uppercase hex; display names live in translations under
# entity.select.<translation_key>.state.<id lowercased> so they can be
# localized -- every device-enum select in this integration works this way.
# washer.py's course comment has the byte-level evidence for why the options[]
# MostUsed_* entry is *not* a trustworthy second source.
#
# Some boards populate /wm/editcourse/vs/0 without ever filling in
# editCourseList itself (issue #1) -- cycle_options() falls back to deriving
# the same list from /course/vs/0's own supportedOptions in that case; see
# _course_codes_from_supported_options for the byte-level evidence.
#
# Shared verbatim by washer, dishwasher, and dryer -- all DA_WM_-family boards
# expose the same /course/vs/0 options contract.
# ---------------------------------------------------------------------------


def hex_pairs(codes):
    """'1C1D21...' -> ['1C', '1D', '21', ...]."""
    return [codes[i:i + 2] for i in range(0, len(codes) - 1, 2)]


def parse_edit_course_list(raw):
    """'EditCourseList_1C1D21...' -> ['1C', '1D', '21', ...]."""
    if not isinstance(raw, str) or '_' not in raw:
        return []
    return hex_pairs(raw.split('_', 1)[1])


def cycle_options(resources):
    rep = resources.get('/wm/editcourse/vs/0') or {}
    codes = parse_edit_course_list(rep.get('x.com.samsung.da.editCourseList'))
    if codes:
        return codes
    return _course_codes_from_supported_options(resources.get('/course/vs/0') or {})


def option_value(options, prefix):
    """Find `<prefix>_<value>` in the options array and return <value>."""
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


def _course_codes_from_supported_options(course_rep):
    """Fallback for an empty/missing editCourseList: derive the selectable
    course list from /course/vs/0's own x.com.samsung.da.supportedOptions
    instead (issue #1: some DA_WM_TP1/TP2-class boards populate the
    /wm/editcourse/vs/0 href but never fill in editCourseList itself).

    supportedOptions is a 1-hex-nibble header followed by one fixed-width
    record per selectable course, self-indexed rather than positional --
    the first byte of every record is that course's own hex code, just in
    the firmware's own internal order, not editCourseList's. Confirmed
    against six independent real-world washer/dryer/dishwasher dumps: every
    one divides evenly into `header + N * K bytes` with fully unique first
    bytes across all N records, at the record's true byte width. (What the
    rest of each record encodes is still unconfirmed -- this only uses the
    course-code byte.)

    Two guards, deliberately conservative rather than guessing further: the
    derived codes must (a) all be distinct -- a real course table, not
    noise -- and (b) include whatever course is currently selected
    (x.com.samsung.da.options' Course_<code> token), which must always be a
    member of its own device's valid list. If no split satisfies both, this
    returns [] rather than guess.

    Among splits that satisfy both, the *smallest* passing K wins, rather
    than requiring a single unambiguous one -- more than one K reliably
    does pass on real data (e.g. the shipped dishwasher fixture: true
    K=7 passes, but so do 10, 14, and 35, none of which are multiples of
    7 -- position 0 always lands on the same real course code regardless
    of K, which is enough on its own to satisfy the current-course guard
    for several unrelated splits). Smallest-K-wins is a heuristic, not a
    proof: it matches the confirmed answer on every one of six independent
    real-world dumps this was checked against, but a coincidentally
    unique, current-course-inclusive *smaller* K is not mathematically
    impossible on some future device, and would be picked silently. Not
    guarded against further here, since course tables are typically large
    enough (double digits) that colliding by chance on both checks is
    unlikely, and no device seen so far actually needs it.
    """
    raw = course_rep.get('x.com.samsung.da.supportedOptions')
    hexstr = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(hexstr, str) or len(hexstr) < 3:
        return []
    body = hexstr[1:]
    if len(body) % 2:
        return []
    total_bytes = len(body) // 2
    current = option_value(course_rep.get('x.com.samsung.da.options'), 'Course')
    for k in range(1, total_bytes + 1):
        if total_bytes % k:
            continue
        n = total_bytes // k
        if n < 2:
            continue
        firsts = [body[i * k * 2:i * k * 2 + 2] for i in range(n)]
        if len(set(firsts)) != n:
            continue
        if current is not None and current not in firsts:
            continue
        return firsts
    return []


def replace_in_options(options, prefix, new_value):
    return [f"{prefix}_{new_value}" if isinstance(o, str) and o.startswith(prefix + '_') else o
            for o in options]


def cycle_write(p, rep, href=None):
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['course', 'vs', '0'], {
        'x.com.samsung.da.options': replace_in_options(opts, 'Course', p),
    }


def _table_id(resources, table_href):
    rep = resources.get(table_href) or {}
    return rep.get('x.com.samsung.da.st.courseTable')


def cycle_select(*, translation_key, icon, table_href=None):
    """A 'Cycle' select over /course/vs/0, labelled from `translation_key`.

    The option list, current value, and write path are all shared across
    washer/dryer/dishwasher; only the translation is family- (and, for
    washer/dryer, board-) specific.

    table_href (washer/dryer only -- see washer.py/dryer.py's call sites)
    suffixes translation_key with the device's own course-table id, read
    from /st/washercourse/vs/0 or /st/dryercourse/vs/0's
    x.com.samsung.da.st.courseTable (e.g. 'washer_cycle' + 'Table_02' ->
    'washer_cycle_table_02'). No table id available at all -- the href
    absent or empty -- gets no translation_key, i.e. the raw course code
    displayed as-is.

    This matters because course codes are NOT guaranteed consistent across
    board generations sharing the same /course/vs/0 contract: every code in
    washer_cycle_table_02 was confirmed against Table_02-reporting devices
    (DA_WM_TP1/TP2 boards); FlexWash's older DA_WM_A51 board reports
    Table_00 instead, so the same hex code could mean a different course
    there for all we've verified. Building the key from whatever table the
    device actually reports, rather than gating a single hardcoded key on
    an exact match, means a table we haven't built translations for yet
    (like Table_00) just falls through Home Assistant's own missing-
    translation handling to the same raw-code display -- exactly what
    happens today for any individual code within a table's translations
    that isn't populated yet -- and adding one later needs new strings.json
    entries, not a code change here.

    Left at its default for dishwasher, which has no equivalent table-id
    resource in any dump seen and no evidence its course codes vary by
    table the way washer/dryer's do -- there's nothing to build a
    table-specific key from.
    """
    key = translation_key
    if table_href is not None:
        def key(resources):
            table = _table_id(resources, table_href)
            return f'{translation_key}_{table.lower()}' if table else None

    return SelectDesc(
        key='cycle', name='Cycle', icon=icon, translation_key=key,
        options=cycle_options,
        exists_fn=lambda rep, resources: bool(cycle_options(resources)),
        rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Course'),
        write_fn=cycle_write,
    )


# ---------------------------------------------------------------------------
# Plain boolean toggles over /course/vs/0's options[] array: a
# '<prefix>_On'/'<prefix>_Off' token, read-modify-written the same way as
# the 'Course' token above. Shared by washer (bubble soak, pre-wash,
# intensive -- issue #22) and dishwasher (storm wash, auto release dry) --
# both families ride this exact contract, just with different prefixes and
# different presence/validation needs on top.
# ---------------------------------------------------------------------------


def bool_option_write(prefix):
    def write(p, rep, href=None):
        if p not in ('On', 'Off'):
            return None
        opts = list(rep.get('x.com.samsung.da.options') or [])
        if not opts:
            return None
        return ['course', 'vs', '0'], {
            'x.com.samsung.da.options': replace_in_options(opts, prefix, p),
        }
    return write


def bool_option_value(prefix):
    return lambda rep: option_value(rep.get('x.com.samsung.da.options'), prefix) == 'On'


def bool_option_exists(prefix):
    return lambda rep, resources: option_value(
        rep.get('x.com.samsung.da.options'), prefix) is not None


def bool_option_switch(key, name, icon, prefix, *, entity_category=None,
                        gate_on_presence=False, validate_fn=None):
    """A SwitchDesc over a '<prefix>_On'/'<prefix>_Off' options[] token.

    gate_on_presence self-gates the entity off on models that never report
    the token at all (washer's bubble soak/pre-wash/intensive); leave False
    for a toggle every device in the family reports (dishwasher's storm
    wash). validate_fn is passed straight through to SwitchDesc for callers
    that need to reject a write against live device state (e.g. washer's
    per-course availability check) -- this factory has no opinion on it and
    building one, if needed, is the caller's job.
    """
    return SwitchDesc(
        key=key, name=name, icon=icon, entity_category=entity_category,
        exists_fn=bool_option_exists(prefix) if gate_on_presence else None,
        rep_fn=bool_option_value(prefix),
        write_fn=bool_option_write(prefix),
        validate_fn=validate_fn,
    )


# ---------------------------------------------------------------------------
# /wm/jobbeginingstatus/vs/0 -- the "why did the cycle not start" reason
# (e.g. door open, no water). The vendor field is x.com.samsung.da.currentStatus
# on every laundry dump that populates it (washer + DA_WM_TP1 dryer). An
# earlier dryer descriptor read x.com.samsung.da.jobBeginingStatus, but no dump
# ever carried that field, so the dryer sensor was always blank -- fixed by
# sharing this one reader.
# ---------------------------------------------------------------------------

JOB_BEGINNING_STATUS = Capability(
    href='/wm/jobbeginingstatus/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='job_beginning_status',
                   field='x.com.samsung.da.currentStatus',
                   name='Job beginning status',
                   entity_category='diagnostic'),
    ),
)

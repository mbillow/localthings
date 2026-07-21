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
    return parse_edit_course_list(rep.get('x.com.samsung.da.editCourseList'))


def option_value(options, prefix):
    """Find `<prefix>_<value>` in the options array and return <value>."""
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


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


def cycle_select(*, translation_key, icon):
    """A 'Cycle' select over /course/vs/0, labelled from `translation_key`.

    The caller supplies the family's translation key (washer_cycle /
    dishwasher_cycle / dryer_cycle) and icon; the option list, current value,
    and write path are all shared.
    """
    return SelectDesc(
        key='cycle', name='Cycle', icon=icon, translation_key=translation_key,
        options=cycle_options,
        exists_fn=lambda rep, resources: bool(cycle_options(resources)),
        rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Course'),
        write_fn=cycle_write,
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

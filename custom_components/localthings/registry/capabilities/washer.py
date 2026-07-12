"""Capabilities specific to washer appliances (Samsung DA_WM_TP1-class
front-load washers).

Resources verified against two live WW90DG6U25LEU4 dumps (Table_02 course
family). Washers never report `oneUiVersion` -- see
`registry/by_type/__init__.py`'s `for_device_by_model()` for the fallback
detection this device type requires.
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc

# ---------------------------------------------------------------------------
# Course_XX hex codes. The 23 codes named in strings.json/translations
# under entity.select.washer_cycle.state.<id, lowercased> were captured
# from a live WW90DG6U25LEU4's x.com.samsung.da.editCourseList
# (EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F96), matched
# positionally against a Slovak-UI user's screenshots of their app's course
# list (same order, same count -- see issue #2) and cross-checked against
# the printed user manual's course table (confirming e.g. '8F' as 'Intense
# Cold', not the position-adjacent-looking but distinct 'Mixed Load', a
# cycle the manual marks "applicable models only" and that does not appear
# in this device's editCourseList -- nor does 'AI Wash', also "applicable
# models only"). FixedCourseList_1C29 (the two courses always pinned in the
# app) maps to '1C'/'29' = Eco 40-60 and Drum Clean+, which matches what
# you'd expect to be pinned (default cycle + maintenance cycle),
# corroborating the positional match.
#
# No static fallback list of those codes is kept here, deliberately: other
# washer models have a different actual course set (a second dump's active
# course, '65', isn't even in the list above; models with 'AI Wash'/'Mixed
# Load' -- both "applicable models only" per the manual -- would have yet
# another set), so hardcoding one device's list would show/hide the wrong
# options on a different model. _cycle_options() below reads only the live
# x.com.samsung.da.editCourseList; if a device doesn't populate that
# resource, the cycle select isn't created at all (see WASHER_COURSE's
# exists_fn). x.com.samsung.da.options' MostUsed_* entry was considered as
# a fallback source (its first byte reliably equals the currently-selected
# Course_XX on both dumps we have), but the bytes after that don't
# correspond to any confirmed course code on either device -- e.g. dump 1's
# MostUsed_1C8410923FA67F00000000000000 decodes to
# ['1C','84','10','92','3F','A6','7F',...] and only '1C' is a real code --
# so it isn't trustworthy as a list of selectable courses and isn't used.
# ---------------------------------------------------------------------------


def _parse_edit_course_list(raw):
    """'EditCourseList_1C1D211B1E29...' -> ['1C', '1D', '21', '1B', '1E', '29', ...]."""
    if not isinstance(raw, str) or '_' not in raw:
        return []
    codes = raw.split('_', 1)[1]
    return [codes[i:i + 2] for i in range(0, len(codes) - 1, 2)]


def _cycle_options(resources):
    rep = resources.get('/wm/editcourse/vs/0') or {}
    return _parse_edit_course_list(rep.get('x.com.samsung.da.editCourseList'))

# ---------------------------------------------------------------------------
# /washer/vs/0 -- wash temperature, spin speed, rinse cycle count
#
# Despite the shared href, this is unrelated to dryer.DRYER_SETTINGS (also
# bound to '/washer/vs/0') -- an artifact of Samsung reusing the same OCF
# path for different device families. Only one of the two ever binds for a
# given device, since dryer and washer are separate by_type registries.
# ---------------------------------------------------------------------------

WASHER_SETTINGS = Capability(
    href='/washer/vs/0',
    entities=(
        SelectDesc(key='wash_temperature', field='x.com.samsung.da.waterTemperature',
                   name='Wash temperature', icon='mdi:thermometer-water',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedWaterTemperature',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.waterTemperature': p})),
        SelectDesc(key='spin_speed', field='x.com.samsung.da.spinLevel',
                   name='Spin speed', icon='mdi:sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedSpinLevel',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.spinLevel': p})),
        SelectDesc(key='rinse_cycles', field='x.com.samsung.da.rinseCycles',
                   name='Rinse cycles', icon='mdi:water-sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedRinseCycles',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.rinseCycles': p})),
    ),
)

# ---------------------------------------------------------------------------
# /course/vs/0 -- selected course, read/write (RMW on the options array,
# same shape as dishwasher.CYCLE_OPTIONS._cycle_write).
# ---------------------------------------------------------------------------

def _option_value(options, prefix):
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


def _replace_in_options(options, prefix, new_value):
    return [f"{prefix}_{new_value}" if isinstance(o, str) and o.startswith(prefix + '_') else o
            for o in options]


def _cycle_write(p, rep, href=None):
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['course', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'Course', p),
    }


WASHER_COURSE = Capability(
    href='/course/vs/0',
    entities=(
        SelectDesc(key='cycle', name='Cycle', icon='mdi:washing-machine',
                   translation_key='washer_cycle',
                   options=_cycle_options,
                   exists_fn=lambda rep, resources: bool(_cycle_options(resources)),
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'Course'),
                   write_fn=_cycle_write),
    ),
)

# ---------------------------------------------------------------------------
# /buzzersound/vs/0 -- buzzer volume and (on some units) a separate finish
# chime. Fields have no 'x.com.samsung.da.' prefix in this resource, unlike
# most other washer hrefs.
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
# /wm/jobbeginingstatus/vs/0 -- same href as dryer.JOB_BEGINNING_STATUS, but
# a different field name (currentStatus, not jobBeginingStatus).
# ---------------------------------------------------------------------------

WASHER_JOB_BEGINNING_STATUS = Capability(
    href='/wm/jobbeginingstatus/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='job_beginning_status',
                   field='x.com.samsung.da.currentStatus',
                   name='Job beginning status',
                   entity_category='diagnostic'),
    ),
)

# ---------------------------------------------------------------------------
# OCF-native / '-vs' fallback pairs for power, kids-lock, remote control.
#
# Same shape as fridge.py's "Aggregate-resource fallbacks": the generic OCF
# href (/power/0, plain boolean 'value') is preferred when present; the
# vendor '-vs' href (richer historically, but for these three controls just
# a string-encoded duplicate) only binds when the generic href is absent
# from this device's resource set, via match_fn. Scoped to washer.py, not
# common.py -- no other device type has been confirmed to expose the
# generic hrefs, so this must not change behavior for dishwasher/dryer/
# oven/refrigerator.
# ---------------------------------------------------------------------------

POWER_GENERIC = Capability(
    href='/power/0',
    entities=(
        SwitchDesc(key='power_switch', field='value',
                   name='Power',
                   value_fn=lambda v: bool(v),
                   write_fn=lambda p, rep, href=None: (
                       ['power', '0'], {'value': p == 'On'})),
    ),
)

POWER_VS_FALLBACK = Capability(
    href='/power/vs/0',
    match_fn=lambda rep, resources: '/power/0' not in resources,
    entities=(
        SwitchDesc(key='power_switch', field='x.com.samsung.da.power',
                   name='Power',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['power', 'vs', '0'],
                       {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

KIDS_LOCK_GENERIC = Capability(
    href='/kidslock/0',
    entities=(
        SwitchDesc(key='child_lock', field='value',
                   name='Child lock', device_class='lock',
                   value_fn=lambda v: bool(v),
                   write_fn=lambda p, rep, href=None: (
                       ['kidslock', '0'], {'value': p == 'On'})),
    ),
)

KIDS_LOCK_VS_FALLBACK = Capability(
    href='/kidslock/vs/0',
    match_fn=lambda rep, resources: '/kidslock/0' not in resources,
    entities=(
        SwitchDesc(key='child_lock', field='x.com.samsung.da.kidsLock',
                   name='Child lock', device_class='lock',
                   value_fn=lambda v: v != 'Ready',
                   write_fn=lambda p, rep, href=None: (
                       ['kidslock', 'vs', '0'],
                       {'x.com.samsung.da.kidsLock': 'Enable' if p == 'On' else 'Ready'})),
    ),
)

REMOTE_CONTROL_GENERIC = Capability(
    href='/remotectrl/0',
    entities=(
        BinarySensorDesc(key='remote_control', field='value',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: bool(v)),
    ),
)

REMOTE_CONTROL_VS_FALLBACK = Capability(
    href='/remotectrl/vs/0',
    match_fn=lambda rep, resources: '/remotectrl/0' not in resources,
    entities=(
        BinarySensorDesc(key='remote_control',
                         field='x.com.samsung.da.remoteControlEnabled',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: str(v).lower() == 'true'),
    ),
)

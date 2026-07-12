"""Capabilities specific to dishwasher appliances (DW9000F-class).

Resources verified against the live device dump at 10.0.0.129.
"""
from ..capability import Capability
from ..entities import ButtonDesc, SelectDesc, SensorDesc, SwitchDesc

# ---------------------------------------------------------------------------
# /dishwasher/vs/0 — cycle wash/dry settings
# ---------------------------------------------------------------------------

DISHWASHER_SETTINGS = Capability(
    href='/dishwasher/vs/0',
    entities=(
        SwitchDesc(key='sanitize', field='x.com.samsung.da.sanitize',
                   name='Sanitize', icon='mdi:bacteria',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['dishwasher', 'vs', '0'],
                       {'x.com.samsung.da.sanitize': 'On' if p == 'On' else 'Off'})),
        SelectDesc(key='heated_dry', field='x.com.samsung.da.heatedDry',
                   name='Smart Dry', icon='mdi:heat-wave',
                   options_field='x.com.samsung.da.supportedHeatedDry',
                   write_fn=lambda p, rep, href=None: (
                       ['dishwasher', 'vs', '0'],
                       {'x.com.samsung.da.heatedDry': p})),
    ),
)

# ---------------------------------------------------------------------------
# /course/vs/0 — cycle selection and course options (RMW on options array)
# ---------------------------------------------------------------------------

# Course IDs are uppercase hex strings matching the Course_XX encoding in
# options[]. Display names are not kept here -- they live in
# strings.json/translations under entity.select.dishwasher_cycle.state.<id,
# lowercased>, same as every other device-enum select in this integration
# (see fridge.py's translation_key entities and select.py's _display()), so
# they can be localized instead of hardcoded to English.
#
# No static fallback list is kept here -- a hardcoded course table would
# show options a given dishwasher model doesn't actually have (or hide ones
# it does). The only trustworthy per-device source is the live
# x.com.samsung.da.editCourseList on /wm/editcourse/vs/0. When that's
# absent (e.g. never populated until the app's course-edit screen has been
# opened at least once), the cycle select isn't created at all -- see
# CYCLE_OPTIONS's exists_fn below. (x.com.samsung.da.options' MostUsed_*
# entry was considered as a second fallback source, but its bytes beyond
# the first don't correspond to any confirmed course code on hardware we
# have dumps for, so it isn't trustworthy either -- see washer.py's
# _cycle_options docstring for the byte-level evidence.)


def _parse_edit_course_list(raw):
    """'EditCourseList_0E07908683848D808E8F' -> ['0E', '07', ...]."""
    if not isinstance(raw, str) or '_' not in raw:
        return []
    codes = raw.split('_', 1)[1]
    return [codes[i:i + 2] for i in range(0, len(codes) - 1, 2)]


def _cycle_options(resources):
    rep = resources.get('/wm/editcourse/vs/0') or {}
    return _parse_edit_course_list(rep.get('x.com.samsung.da.editCourseList'))


def _option_value(options, prefix):
    """Find `<prefix>_<value>` in options array and return <value>."""
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


def _storm_wash_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['course', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'StormWashZone', p),
    }


def _auto_release_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['course', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'AutoDoorRelease', p),
    }


CYCLE_OPTIONS = Capability(
    href='/course/vs/0',
    entities=(
        SelectDesc(key='cycle', name='Cycle', icon='mdi:dishwasher',
                   translation_key='dishwasher_cycle',
                   options=_cycle_options,
                   exists_fn=lambda rep, resources: bool(_cycle_options(resources)),
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'Course'),
                   write_fn=_cycle_write),
        SwitchDesc(key='storm_wash', name='Storm Wash+', icon='mdi:weather-lightning-rainy',
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'StormWashZone') == 'On',
                   write_fn=_storm_wash_write),
        SwitchDesc(key='auto_release_dry', name='Auto release dry', icon='mdi:door-open',
                   exists_fn=lambda rep, resources: any(
                       isinstance(o, str) and o.startswith('AutoDoorRelease_')
                       for o in (rep.get('x.com.samsung.da.options') or [])
                   ),
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'AutoDoorRelease') == 'On',
                   write_fn=_auto_release_write),
    ),
)

# ---------------------------------------------------------------------------
# Self-diagnostic trigger and last-operation-source sensor
# ---------------------------------------------------------------------------

DIAGNOSIS = Capability(
    href='/diagnosis/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='diagnosis_status', field='x.com.samsung.da.diagnosisStart',
                   name='Diagnosis status', icon='mdi:stethoscope',
                   entity_category='diagnostic'),
        ButtonDesc(key='diagnosis_start', field='', name='Start diagnosis',
                   payload='Start', icon='mdi:play-circle-outline',
                   entity_category='diagnostic',
                   write_fn=lambda p, rep, href=None: (
                       ['diagnosis', 'vs', '0'], {'x.com.samsung.da.diagnosisStart': p})),
    ),
)

OPERATION_ORIGIN = Capability(
    href='/operation/origin/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='operation_origin', field='origin',
                   name='Last operation source', icon='mdi:remote',
                   entity_category='diagnostic'),
    ),
)

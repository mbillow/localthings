"""Capabilities specific to dishwasher appliances (DW9000F-class).

Resources verified against the live device dump at 10.0.0.129.
"""
from ..capability import Capability
from ..entities import SelectDesc, SwitchDesc

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

# Course IDs in editCourseList byte order, matched to Samsung app display names.
# IDs are uppercase hex strings matching the Course_XX encoding in options[].
_COURSE_ID_TO_NAME: dict[str, str] = {
    '0E': 'AI Wash',
    '07': 'Pre blast',
    '90': 'Self clean',
    '86': 'Normal',
    '83': 'Express 60',
    '84': 'Heavy',
    '8D': 'Pots and pans',
    '80': 'Delicate',
    '8E': 'Plastic',
    '8F': 'Baby Care',
}
_COURSE_NAME_TO_ID = {v: k for k, v in _COURSE_ID_TO_NAME.items()}
_CYCLE_OPTIONS = tuple(_COURSE_ID_TO_NAME.values())


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
    course_id = _COURSE_NAME_TO_ID.get(p)
    if not course_id:
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['course', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'Course', course_id),
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
                   options=_CYCLE_OPTIONS,
                   rep_fn=lambda rep: _COURSE_ID_TO_NAME.get(
                       _option_value(rep.get('x.com.samsung.da.options'), 'Course')
                   ),
                   write_fn=_cycle_write),
        SwitchDesc(key='storm_wash', name='Storm Wash+', icon='mdi:weather-lightning-rainy',
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'StormWashZone') == 'On',
                   write_fn=_storm_wash_write),
        SwitchDesc(key='auto_release_dry', name='Auto release dry', icon='mdi:door-open',
                   exists_fn=lambda rep: any(
                       isinstance(o, str) and o.startswith('AutoDoorRelease_')
                       for o in (rep.get('x.com.samsung.da.options') or [])
                   ),
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'AutoDoorRelease') == 'On',
                   write_fn=_auto_release_write),
    ),
)

"""Capabilities for the oven family (Samsung NV7000BS-class).

Resources verified against the live device via DTLS-CoAP.
See `local-tools/comparisons/oven-tree.md` for the full field reference.

Write surfaces this module exposes:

  proven:
    * Lamp via /mode/vs/0 options RMW (probe_oven_lamp_toggle.py)
      — works even with Remote Control off.

  unproven (first HA use is also the test):
    * Sound, FastPreheat, NaturalSteam — same RMW pattern as lamp.
    * Setpoint via /temperatures/vs/0 items RMW.
    * Cook time via /operational/state/vs/0 operationTime/remainingTime.
    * Mode select via /mode/vs/0 .modes — mid-cook acceptance unknown.
    * Stop via /operational/state/vs/0 state='Ready'.

Note: Cycle start is not implemented. Reverse-engineering shows local-OCF
cycle start is not reproducible on this firmware (see project_oven_remote
_start_open.md). Mode writes are also unreliable — the oven rolls them back
once a cycle is active. OVEN_MODE is provided as a SelectDesc for fidelity
but is effectively read-only in practice.
"""
from datetime import datetime, timezone, timedelta

from ..capability import Capability
from ..entities import (
    BinarySensorDesc, ButtonDesc, NumberDesc, SelectDesc, SensorDesc,
    SwitchDesc,
)
from .common import normalize_temp_unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SETPOINT_MIN_C = 30
SETPOINT_MAX_C = 270
SETPOINT_STEP_C = 5

# Verified against issue #44's range dump (NSI6DG9100SRAA, unit reported as
# "Fahrenheit" on /temperatures/vs/0): Bake mode's modeSpec on /mode/vs/0
# reports tempMinF/tempMaxF/tempIntervalF = 175/550/5. Kept as a separate
# constant set rather than converted from the Celsius bounds above, which
# are themselves unverified (no live dump; see module docstring).
SETPOINT_MIN_F = 175
SETPOINT_MAX_F = 550
SETPOINT_STEP_F = 5

# Mode options seen on NV7000BS-class. No dump exists so this list is inferred
# from Samsung documentation and firmware observations. The firmware will
# reject unknown modes; missing entries here are a coverage gap, not a bug.
_OVEN_MODES = (
    'NoOperation',
    'Bake',
    'Broil',
    'Convection',
    'ConvectionBake',
    'ConvectionBroil',
    'FrozenPizzaPlus',
    'SlowCook',
    'PlateWarm',
    'AirFry',
)

_SAMSUNG_STATE_TO_OCF = {
    'Ready':   'idle',
    'Run':     'active',
    'Running': 'active',
    'Pause':   'pause',
    'Paused':  'pause',
    'End':     'idle',
    'Stop':    'idle',
}


def _to_ocf(v):
    return _SAMSUNG_STATE_TO_OCF.get(v, v) if v is not None else None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _finish_time(remaining_str):
    if not remaining_str:
        return None
    try:
        h, m, s = remaining_str.split(':')
        total_s = int(h) * 3600 + int(m) * 60 + int(s)
        if total_s == 0:
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=total_s)
    except Exception:
        return None


def _op_minutes(op_time):
    """Parse 'H:MM:SS' operationTime into integer minutes."""
    if not op_time:
        return None
    try:
        h, m, s = op_time.split(':')
        return int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Options-array helpers (shared by lamp, sound, fastpreheat, naturalsteam)
# ---------------------------------------------------------------------------

def _option_value(options, prefix):
    """Find `<prefix>_<value>` in an options array and return <value>."""
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


def _replace_in_options(options, prefix, new_value):
    """Return a new options list with the `<prefix>_*` slot replaced."""
    return [f"{prefix}_{new_value}" if o.startswith(prefix + '_') else o
            for o in options]


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

def _oven_setpoint_write(p, rep, href=None):
    """RMW write to /temperatures/vs/0 items array."""
    try:
        temp = float(p)
    except (TypeError, ValueError):
        return None
    min_v, max_v, step_v = _setpoint_bounds(rep)
    temp_i = int(round(temp / step_v) * step_v)
    if not (min_v <= temp_i <= max_v):
        return None
    items = rep.get('x.com.samsung.da.items')
    if not items:
        return None
    items = [dict(it) for it in items]
    items[0]['x.com.samsung.da.desired'] = str(temp_i)
    return ['temperatures', 'vs', '0'], {'x.com.samsung.da.items': items}


def _cook_time_write(p, rep, href=None):
    """Write operationTime + remainingTime (H:MM:SS) from minutes."""
    try:
        minutes = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    if not (0 <= minutes <= 1439):
        return None
    h, m = divmod(minutes, 60)
    hms = f"{h:02d}:{m:02d}:00"
    return ['operational', 'state', 'vs', '0'], {
        'x.com.samsung.da.operationTime': hms,
        'x.com.samsung.da.remainingTime': hms,
    }


def _stop_write(p, rep, href=None):
    return ['operational', 'state', 'vs', '0'], {
        'x.com.samsung.da.state': 'Ready',
    }


def _oven_mode_write(p, rep, href=None):
    if p not in _OVEN_MODES:
        return None
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [p]}


def _lamp_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'UpperLamp', p),
    }


def _sound_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'Sound', p),
    }


def _fastpreheat_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'fastpreheat', p),
    }


def _naturalsteam_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    if not any(o.startswith('NaturalSteam_') for o in opts):
        opts = opts + [f'NaturalSteam_{p}']
    else:
        opts = _replace_in_options(opts, 'NaturalSteam', p)
    return ['mode', 'vs', '0'], {'x.com.samsung.da.options': opts}


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

OVEN_OPERATIONAL_STATE = Capability(
    href='/operational/state/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='machine_state', field='x.com.samsung.da.state',
                   name='Machine state', icon='mdi:stove',
                   device_class='enum', options=('idle', 'active', 'pause'),
                   translation_key='machine_state', value_fn=_to_ocf),
        BinarySensorDesc(key='cycle_active', field='x.com.samsung.da.state',
                         name='Cycle active', device_class='running',
                         value_fn=lambda v: _SAMSUNG_STATE_TO_OCF.get(v) == 'active'),
        SensorDesc(key='progress_percentage',
                   field='x.com.samsung.da.progressPercentage',
                   name='Progress percent', unit='%', state_class='measurement',
                   value_fn=_int),
        SensorDesc(key='operation_time_minutes',
                   field='x.com.samsung.da.operationTime',
                   name='Operation time (minutes)', unit='min',
                   state_class='measurement', value_fn=_op_minutes),
        SensorDesc(key='finish_time', field='x.com.samsung.da.remainingTime',
                   name='Estimated finish', device_class='timestamp',
                   value_fn=_finish_time),
        NumberDesc(key='cook_time', field='x.com.samsung.da.operationTime',
                   name='Cook time', unit='min', native_min=0, native_max=1439,
                   step=1.0, icon='mdi:timer', value_fn=_op_minutes,
                   write_fn=_cook_time_write),
        ButtonDesc(key='stop', field='', name='Stop cycle', icon='mdi:stop',
                   payload='Stop', write_fn=_stop_write),
    ),
)

OVEN_CAVITY = Capability(
    href='/oven/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='oven_state', field='x.com.samsung.da.state',
                   name='Cavity state'),
    ),
)

def _oven_temp_unit(rep):
    """Same shape/risk as fridge.py's TEMPERATURES_FALLBACK: this is the
    same aggregate `/temperatures/vs/0` items[] resource type, which on
    fridge hardware carries a per-item `x.com.samsung.da.unit` field
    ('Celsius'/'Fahrenheit') that was previously hardcoded away (issue #7).
    Keeps the verified '°C' default when the field is absent (the original
    NV7000BS-class dump this module was written against), but reads it live
    -- issue #44's range dump is the first to report 'Fahrenheit' here."""
    items = rep.get('x.com.samsung.da.items') or []
    unit = items[0].get('x.com.samsung.da.unit') if items else None
    return normalize_temp_unit(unit, default='°C')


def _setpoint_bounds(rep):
    """(min, max, step) for the live unit -- see the SETPOINT_*_C/_F
    constants above for provenance. Bounds must track the unit shown by
    unit_fn (both read the same live rep), or the HA slider's range would
    silently mismatch its own displayed unit."""
    if _oven_temp_unit(rep) == '°F':
        return SETPOINT_MIN_F, SETPOINT_MAX_F, SETPOINT_STEP_F
    return SETPOINT_MIN_C, SETPOINT_MAX_C, SETPOINT_STEP_C


OVEN_SETPOINT = Capability(
    href='/temperatures/vs/0',
    poll_tier='hot',
    entities=(
        # NumberDesc first — test_oven_setpoint_write_is_read_modify_write uses entities[0]
        NumberDesc(key='oven_setpoint', field='x.com.samsung.da.items',
                   name='Setpoint', device_class='temperature', unit_fn=_oven_temp_unit,
                   native_min=float(SETPOINT_MIN_C), native_max=float(SETPOINT_MAX_C),
                   step=float(SETPOINT_STEP_C), icon='mdi:thermometer-chevron-up',
                   native_min_fn=lambda rep: float(_setpoint_bounds(rep)[0]),
                   native_max_fn=lambda rep: float(_setpoint_bounds(rep)[1]),
                   step_fn=lambda rep: float(_setpoint_bounds(rep)[2]),
                   value_fn=lambda items: _int(
                       (items[0].get('x.com.samsung.da.desired') if items else None)),
                   write_fn=_oven_setpoint_write),
        SensorDesc(key='current_temp_c', field='x.com.samsung.da.items',
                   name='Temperature', device_class='temperature',
                   state_class='measurement', unit_fn=_oven_temp_unit,
                   value_fn=lambda items: _int(
                       (items[0].get('x.com.samsung.da.current') if items else None))),
    ),
)

OVEN_DOOR = Capability(
    href='/doors/vs/0',
    poll_tier='hot',
    entities=(
        BinarySensorDesc(key='door_open', field='x.com.samsung.da.items',
                         name='Door', device_class='door',
                         value_fn=lambda items: (
                             items[0].get('x.com.samsung.da.openState') == 'Open'
                             if items else None)),
    ),
)

OVEN_CONNECTED = Capability(
    href='/connected/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='cloud_connected', field='x.com.samsung.da.connected',
                         name='Cloud connected', device_class='connectivity',
                         entity_category='diagnostic',
                         value_fn=lambda v: v == 'On'),
    ),
)

# Static cavity capability metadata (count/type/supported features) -- no
# per-cavity data varies at runtime on any dump seen so far (issue #44's
# range: single cavity, no supportedFeatureList entries). Bound with no
# entities purely for coverage; revisit if a multi-cavity dump surfaces
# fields worth exposing.
OVEN_SPEC = Capability(href='/oven/spec/vs/0')

OVEN_MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        # SelectDesc first — test_oven_mode_options_nonempty uses entities[0]
        SelectDesc(key='oven_mode', field='x.com.samsung.da.modes',
                   name='Cooking mode', icon='mdi:tune',
                   options=_OVEN_MODES,
                   value_fn=lambda v: v[0] if v else None,
                   write_fn=_oven_mode_write),
        SwitchDesc(key='lamp', field='x.com.samsung.da.options',
                   name='Lamp', icon='mdi:track-light',
                   value_fn=lambda opts: _option_value(opts, 'UpperLamp') == 'On',
                   write_fn=_lamp_write),
        SwitchDesc(key='sound', field='x.com.samsung.da.options',
                   name='Sound', icon='mdi:volume-high',
                   entity_category='config',
                   value_fn=lambda opts: _option_value(opts, 'Sound') == 'On',
                   write_fn=_sound_write),
        SwitchDesc(key='fast_preheat', field='x.com.samsung.da.options',
                   name='Fast preheat', icon='mdi:fire',
                   value_fn=lambda opts: _option_value(opts, 'fastpreheat') == 'On',
                   write_fn=_fastpreheat_write),
        SwitchDesc(key='natural_steam', field='x.com.samsung.da.options',
                   name='Natural steam', icon='mdi:kettle-steam',
                   value_fn=lambda opts: _option_value(opts, 'NaturalSteam') == 'On',
                   write_fn=_naturalsteam_write),
    ),
)

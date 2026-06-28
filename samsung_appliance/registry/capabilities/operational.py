"""Operational-state capability: machine state, progress, remaining-time.

Carries the rare cross-field hooks (active_when, on_observation, project) for
remaining-time extrapolation. Shared by dryer/dishwasher/oven/washer families.
"""
import time

from ..capability import Capability
from ..entities import ButtonDesc, SensorDesc

_SAMSUNG_STATE_TO_OCF = {
    'Ready': 'idle', 'Run': 'active', 'Running': 'active',
    'Pause': 'pause', 'Paused': 'pause', 'End': 'idle', 'Stop': 'idle',
}


def _to_ocf(v):
    return _SAMSUNG_STATE_TO_OCF.get(v, v) if v is not None else None


def _progress(v):
    return 'Idle' if v in (None, 'None') else v


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _active_when(rep):
    return _SAMSUNG_STATE_TO_OCF.get(rep.get('x.com.samsung.da.state')) == 'active'


def _on_observation(state, rep):
    rem = rep.get('x.com.samsung.da.remainingTime')
    if not isinstance(rem, str):
        return
    try:
        h, m, s = rem.split(':')
        state['remaining_anchor'] = (time.time(),
                                     int(h) * 3600 + int(m) * 60 + int(s))
    except (ValueError, AttributeError):
        pass


def _project(state, sensors):
    anchor = state.get('remaining_anchor')
    if sensors.get('machine_state') != 'active' or anchor is None:
        return sensors
    ts, total = anchor
    remaining = max(0, int(total - (time.time() - ts)))
    h, rest = divmod(remaining, 3600)
    m, s = divmod(rest, 60)
    sensors = dict(sensors)
    sensors['completion_time'] = f"{h}:{m:02d}:{s:02d}"
    sensors['completion_minutes'] = h * 60 + m + (1 if s > 0 else 0)
    return sensors


def _rem_minutes(remaining):
    if not remaining:
        return None
    try:
        h, m, s = remaining.split(':')
        return int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
    except Exception:
        return None


OPERATIONAL_STATE = Capability(
    href='/operational/state/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='machine_state', field='x.com.samsung.da.state',
                   name='Machine state', value_fn=_to_ocf),
        SensorDesc(key='progress', field='x.com.samsung.da.progress',
                   name='Progress', icon='mdi:progress-wrench', value_fn=_progress),
        SensorDesc(key='progress_percentage',
                   field='x.com.samsung.da.progressPercentage',
                   name='Progress percent', unit='%', state_class='measurement',
                   value_fn=_int),
        SensorDesc(key='completion_time', field='x.com.samsung.da.remainingTime',
                   name='Completion time', icon='mdi:timer-sand'),
        SensorDesc(key='completion_minutes', field='x.com.samsung.da.remainingTime',
                   name='Remaining minutes', unit='min', device_class='duration',
                   state_class='measurement',
                   value_fn=lambda r: _rem_minutes(r)),
        SensorDesc(key='delay_start_time', field='x.com.samsung.da.delayStartTime',
                   name='Delay start time', icon='mdi:timer-pause'),
        ButtonDesc(key='start', field='', name='Start cycle', payload='Run',
                   icon='mdi:play',
                   write_fn=lambda p, rep: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
        ButtonDesc(key='pause', field='', name='Pause cycle', payload='Pause',
                   icon='mdi:pause',
                   write_fn=lambda p, rep: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
        ButtonDesc(key='stop', field='', name='Stop cycle', payload='Ready',
                   icon='mdi:stop',
                   write_fn=lambda p, rep: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
    ),
    active_when=_active_when,
    on_observation=_on_observation,
    project=_project,
)

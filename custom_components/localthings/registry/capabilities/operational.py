"""Operational-state capability: machine state, progress, remaining-time.

Shared by dryer/dishwasher/oven/washer families.
"""
from datetime import datetime, timezone, timedelta, time as dt_time

from ..capability import Capability
from ..entities import BinarySensorDesc, ButtonDesc, SensorDesc, TimeDesc

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


def _parse_hms(v):
    if not v:
        return None
    try:
        h, m, s = v.split(':')
        return dt_time(int(h) % 24, int(m), int(s))
    except Exception:
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


OPERATIONAL_STATE = Capability(
    href='/operational/state/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='machine_state', field='x.com.samsung.da.state',
                   name='Machine state', value_fn=_to_ocf),
        # cycle_active is a bool derived from machine_state; used by the
        # adapter to gate oven writes (cycle_active_field='cycle_active').
        # Harmless for non-oven appliances — just an extra bool in state.
        # Samsung firmware keeps state='Run' after progress reaches 'Finish',
        # so we also gate on progress to avoid a stuck 'Running' indication.
        BinarySensorDesc(key='cycle_active', device_class='running',
                         name='Cycle active',
                         rep_fn=lambda rep: (
                             _SAMSUNG_STATE_TO_OCF.get(rep.get('x.com.samsung.da.state')) == 'active'
                             and rep.get('x.com.samsung.da.progress') != 'Finish'
                         )),
        SensorDesc(key='progress', name='Progress', icon='mdi:progress-wrench',
                   rep_fn=lambda rep: (
                       'Idle' if _SAMSUNG_STATE_TO_OCF.get(rep.get('x.com.samsung.da.state')) != 'active'
                       else _progress(rep.get('x.com.samsung.da.progress'))
                   )),
        SensorDesc(key='progress_percentage',
                   field='x.com.samsung.da.progressPercentage',
                   name='Progress percent', unit='%', state_class='measurement',
                   value_fn=_int),
        # Only show finish time when machine is actively running. Samsung
        # firmware leaves a stale remainingTime after a cycle ends, and
        # freezes it at '00:01:00' when progress reaches 'Finish'.
        SensorDesc(key='finish_time', device_class='timestamp',
                   name='Estimated finish',
                   rep_fn=lambda rep: (
                       None if _SAMSUNG_STATE_TO_OCF.get(rep.get('x.com.samsung.da.state')) != 'active'
                            or rep.get('x.com.samsung.da.progress') == 'Finish'
                       else _finish_time(rep.get('x.com.samsung.da.remainingTime'))
                   )),
        TimeDesc(key='delay_start_time', field='x.com.samsung.da.delayStartTime',
                 name='Delay start',
                 value_fn=_parse_hms,
                 write_fn=lambda p, rep, href=None: (
                     ['operational', 'state', 'vs', '0'],
                     {'x.com.samsung.da.delayStartTime': f'{p.hour}:{p.minute:02d}:{p.second:02d}'})),
        ButtonDesc(key='start', field='', name='Start cycle', payload='Run',
                   icon='mdi:play',
                   write_fn=lambda p, rep, href=None: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
        ButtonDesc(key='pause', field='', name='Pause cycle', payload='Pause',
                   icon='mdi:pause',
                   write_fn=lambda p, rep, href=None: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
        ButtonDesc(key='stop', field='', name='Stop cycle', payload='Ready',
                   icon='mdi:stop',
                   write_fn=lambda p, rep, href=None: (
                       ['operational', 'state', 'vs', '0'],
                       {'x.com.samsung.da.state': p})),
    ),
)

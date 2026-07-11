"""Operational-state capability: machine state, progress, remaining-time.

Shared by dryer/dishwasher/oven/washer families.
"""
from datetime import datetime, timezone, timedelta

from ..capability import Capability
from ..entities import BinarySensorDesc, ButtonDesc, NumberDesc, SensorDesc

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


def _delay_hours(v):
    """delayStartTime is a duration until the cycle starts, not a
    wall-clock time -- "01:00" means "1 hour from when you press start",
    not "1 AM"."""
    if not v:
        return 0.0
    try:
        h, m, s = v.split(':')
        return int(h) + int(m) / 60 + int(s) / 3600
    except Exception:
        return None


def _format_delay(hours):
    total_minutes = round(max(float(hours), 0) * 60)
    h, m = divmod(total_minutes, 60)
    return f'{h}:{m:02d}:00'


def _delay_field(rep):
    """Washer hardware reports the delay-until-start duration under
    'delayEndTime' instead of 'delayStartTime' (both hold a duration, not a
    wall-clock time -- see _delay_hours). Write back whichever key the
    device itself is using; default to delayStartTime for hardware that
    reports neither yet (matches prior behavior)."""
    return ('x.com.samsung.da.delayEndTime' if 'x.com.samsung.da.delayEndTime' in rep
            else 'x.com.samsung.da.delayStartTime')


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
        NumberDesc(key='delay_start_hours', name='Delay start', icon='mdi:timer-plus-outline',
                   device_class='duration', unit='h',
                   native_min=0, native_max=24, step=1,
                   rep_fn=lambda rep: _delay_hours(
                       rep.get('x.com.samsung.da.delayStartTime')
                       or rep.get('x.com.samsung.da.delayEndTime')),
                   write_fn=lambda p, rep, href=None: (
                       ['operational', 'state', 'vs', '0'],
                       {_delay_field(rep): _format_delay(p)})),
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

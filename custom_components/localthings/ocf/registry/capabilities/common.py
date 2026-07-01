"""Capabilities shared across multiple appliance families.

Each capability is keyed on the stable OCF resource href (not rt), verified
against live device dumps:
  /kidslock/vs/0            -> x.com.samsung.da.kidsLock
  /remotectrl/vs/0          -> x.com.samsung.da.remoteControlEnabled
  /power/vs/0               -> x.com.samsung.da.power
  /alarms/vs/0              -> x.com.samsung.da.items
  /energy/consumption/vs/0  -> x.com.samsung.da.instantaneousPower / cumulativePower
  /water/consumption/vs/0   -> x.com.samsung.da.cumulativeWater
  /filter/waterfilter/vs/0  -> x.com.samsung.da.filterUsage / filterStatus
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc, SwitchDesc


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp_power(v):
    n = _num(v)
    return 0.0 if (n is not None and n < 0) else n


def _wh_to_kwh(v):
    n = _num(v)
    return round(n / 1000.0, 2) if n is not None else None


def _ml_to_l(v):
    n = _num(v)
    return round(n / 1000.0, 1) if n is not None else None


def _active_alarm_codes(items):
    if not items or not isinstance(items, list):
        return 'none'
    codes = [i.get('x.com.samsung.da.code') for i in items if i.get('x.com.samsung.da.code')]
    return ', '.join(codes) if codes else 'none'


KIDS_LOCK = Capability(
    href='/kidslock/vs/0',
    entities=(
        SwitchDesc(key='child_lock', field='x.com.samsung.da.kidsLock',
                   name='Child lock', device_class='lock',
                   value_fn=lambda v: v != 'Ready',
                   write_fn=lambda p, rep, href=None: (
                       ['kidslock', 'vs', '0'],
                       {'x.com.samsung.da.kidsLock': 'Enable' if p == 'On' else 'Ready'})),
    ),
)

REMOTE_CONTROL = Capability(
    href='/remotectrl/vs/0',
    entities=(
        BinarySensorDesc(key='remote_control',
                         field='x.com.samsung.da.remoteControlEnabled',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: str(v).lower() == 'true'),
    ),
)

POWER = Capability(
    href='/power/vs/0',
    entities=(
        SwitchDesc(key='power_switch', field='x.com.samsung.da.power',
                   name='Power',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (['power', 'vs', '0'], {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

ALARMS = Capability(
    href='/alarms/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='alarm_code', field='x.com.samsung.da.items',
                   name='Alarm code', icon='mdi:alert',
                   entity_category='diagnostic', value_fn=_active_alarm_codes),
    ),
)

ENERGY_METER = Capability(
    href='/energy/consumption/vs/0',
    entities=(
        SensorDesc(key='power_watts', field='x.com.samsung.da.instantaneousPower',
                   name='Power', device_class='power', state_class='measurement',
                   unit='W', value_fn=_clamp_power),
        SensorDesc(key='energy_kwh', field='x.com.samsung.da.cumulativePower',
                   name='Energy', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=_wh_to_kwh),
    ),
)

WATER_METER = Capability(
    href='/water/consumption/vs/0',
    entities=(
        SensorDesc(key='water_liters', field='x.com.samsung.da.cumulativeWater',
                   name='Water consumption', device_class='water',
                   state_class='total_increasing', unit='L', icon='mdi:water',
                   value_fn=_ml_to_l),
    ),
)

WATER_FILTER = Capability(
    href='/filter/waterfilter/vs/0',
    match_fn=lambda rep, _: rep.get('x.com.samsung.da.filterStatus', '').lower() != 'notused',
    entities=(
        SensorDesc(key='filter_usage', field='x.com.samsung.da.filterUsage',
                   name='Filter usage', unit='%', state_class='measurement',
                   icon='mdi:filter'),
        SensorDesc(key='filter_status', field='x.com.samsung.da.filterStatus',
                   name='Filter status', icon='mdi:filter-check'),
    ),
)

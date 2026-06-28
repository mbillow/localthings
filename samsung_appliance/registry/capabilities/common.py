"""Capabilities shared across multiple appliance families.

rt strings verified against live device dumps:
  /kidslock/vs/0        -> x.com.samsung.da.operation
  /remotectrl/vs/0      -> x.com.samsung.da.configuration
  /power/vs/0           -> x.com.samsung.da.operation  (shares rt with kidslock)
  /alarms/vs/0          -> x.com.samsung.da.alarms
  /energy/consumption/vs/0  -> x.com.samsung.da.energyconsumption
  /water/consumption/vs/0   -> x.com.samsung.da.waterconsumption
  /filter/waterfilter/vs/0  -> x.com.samsung.da.filter.water

NOTE: KIDS_LOCK and POWER share the same rt (x.com.samsung.da.operation).
When building a registry dict keyed by rt, only one can be present.
They target different hrefs (/kidslock/vs/0 vs /power/vs/0) with different
field keys — at runtime the absent field returns None and is filtered.
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc


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


def _last_alarm_code(items):
    if items and isinstance(items, list):
        return items[-1].get('x.com.samsung.da.code')
    return None


KIDS_LOCK = Capability(
    rt='x.com.samsung.da.operation',
    entities=(
        BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock',
                         name='Child lock', device_class='lock',
                         value_fn=lambda v: v != 'Ready'),
    ),
)

REMOTE_CONTROL = Capability(
    rt='x.com.samsung.da.configuration',
    entities=(
        BinarySensorDesc(key='remote_control',
                         field='x.com.samsung.da.remoteControlEnabled',
                         name='Remote control', device_class='connectivity',
                         value_fn=lambda v: str(v).lower() == 'true'),
    ),
)

# POWER shares rt with KIDS_LOCK (x.com.samsung.da.operation).
# Do not include both in the same registry dict simultaneously.
POWER = Capability(
    rt='x.com.samsung.da.operation',
    entities=(
        BinarySensorDesc(key='power_state', field='x.com.samsung.da.power',
                         name='Power', device_class='power',
                         value_fn=lambda v: v == 'On'),
    ),
)

ALARMS = Capability(
    rt='x.com.samsung.da.alarms',
    entities=(
        SensorDesc(key='alarm_code', field='x.com.samsung.da.items',
                   name='Alarm code', icon='mdi:alert',
                   entity_category='diagnostic', value_fn=_last_alarm_code),
    ),
)

ENERGY_METER = Capability(
    rt='x.com.samsung.da.energyconsumption',
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
    rt='x.com.samsung.da.waterconsumption',
    entities=(
        SensorDesc(key='water_liters', field='x.com.samsung.da.cumulativeWater',
                   name='Water consumption', device_class='water',
                   state_class='total_increasing', unit='L', icon='mdi:water',
                   value_fn=_ml_to_l),
    ),
)

WATER_FILTER = Capability(
    rt='x.com.samsung.da.filter.water',
    entities=(
        SensorDesc(key='filter_usage', field='x.com.samsung.da.filterUsage',
                   name='Filter usage', unit='%', state_class='measurement',
                   icon='mdi:filter'),
        SensorDesc(key='filter_status', field='x.com.samsung.da.filterStatus',
                   name='Filter status', icon='mdi:filter-check'),
    ),
)

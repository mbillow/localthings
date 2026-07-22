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


def clamp_power(v):
    n = _num(v)
    return 0.0 if (n is not None and n < 0) else n


def wh_to_kwh(v):
    n = _num(v)
    return round(n / 1000.0, 2) if n is not None else None


def normalize_temp_unit(raw, default='°F'):
    """'C'/'Celsius' -> '°C', 'F'/'Fahrenheit' -> '°F'. Falls back to
    `default` for any other/missing value. Shared by fridge.py and oven.py,
    both of which read a per-device unit off a `/temperature*` resource
    instead of assuming one (see fridge.py's module docstring, issue #7)."""
    raw = (raw or '').strip().upper()
    if raw.startswith('C'):
        return '°C'
    if raw.startswith('F'):
        return '°F'
    return default


def _ml_to_l(v):
    n = _num(v)
    return round(n / 1000.0, 1) if n is not None else None


def _active_alarm_codes(items):
    if not items or not isinstance(items, list):
        return 'none'
    codes = [i.get('x.com.samsung.da.code') for i in items if i.get('x.com.samsung.da.code')]
    return ', '.join(codes) if codes else 'none'


# OCF-native / vendor '-vs' fallback pairs for power, kids-lock, remote control.
#
# These three controls exist as both a standard OCF resource (/power/0,
# oic.r.switch.binary, plain boolean 'value') and a Samsung vendor resource
# (/power/vs/0, x.com.samsung.da.power) -- Samsung advertises both as its
# firmware migrates onto the OCF standard model. Prefer the OCF-standard href
# when the device exposes it; the '-vs' href (a string-encoded duplicate for
# these three) binds only when the generic href is absent, via match_fn. Older
# firmware has only the '-vs' resource, so the pair is behaviour-identical to a
# lone '-vs' cap there. See the adding-device-support skill's "OCF-standard vs
# vendor" section for why this is preferred-non-vs-with-fallback, not a blanket
# choice. Every device registry lists both caps of each pair.

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

ALARMS = Capability(
    href='/alarms/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='alarm_code', field='x.com.samsung.da.items',
                   name='Alarm code', icon='mdi:alert',
                   entity_category='diagnostic', value_fn=_active_alarm_codes),
    ),
)

# instantaneousPower is a dead field on DA_WM_-class laundry dumps (washers and
# the issue #14 dryer) and on dishwashers too: the literal sentinel '-500',
# unchanged across off/idle/running. clamp_power floors it to a misleading
# "0 W" that reads as a real idle measurement. Gate power_watts out when the
# sentinel is seen -- but only then, so a device reporting a real value (e.g. a
# fridge's 93 W) still shows it (issue #6). cumulativePower is absent on at
# least one washer model; the exists_fn makes that explicit rather than relying
# on the generic field-presence gate.
_DEAD_INSTANTANEOUS_POWER = '-500'

ENERGY_METER = Capability(
    href='/energy/consumption/vs/0',
    entities=(
        # `not rep` keeps the empty-{} stub carve-out (see entity._is_included):
        # an explicit exists_fn otherwise bypasses it, which would drop the
        # entity when /device/0 returns a not-yet-fetched stub. On a populated
        # rep, hide power only for the dead sentinel or an absent field.
        SensorDesc(key='power_watts', field='x.com.samsung.da.instantaneousPower',
                   name='Power', device_class='power', state_class='measurement',
                   unit='W', value_fn=clamp_power,
                   exists_fn=lambda rep, resources: not rep or (
                       rep.get('x.com.samsung.da.instantaneousPower')
                       not in (None, _DEAD_INSTANTANEOUS_POWER))),
        SensorDesc(key='energy_kwh', field='x.com.samsung.da.cumulativePower',
                   name='Energy', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.cumulativePower' in rep)),
        # cumulativeConsumption is a second, independently-varying running
        # total alongside cumulativePower -- some fridges (issue #26) report
        # both. Self-gates off where only cumulativePower is present.
        SensorDesc(key='power_energy_kwh', field='x.com.samsung.da.cumulativeConsumption',
                   name='Power energy', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: 'x.com.samsung.da.cumulativeConsumption' in rep),
        # AI Energy Mode's lifetime savings estimate vs. an unoptimized
        # baseline -- present on some models (e.g. TP1X_REF_21K, issue #21/
        # #27) and absent on others (issue #20/#26), unlike cumulativePower.
        SensorDesc(key='energy_saved_kwh', field='x.com.samsung.da.cumulativeSavedPower',
                   name='Energy saved', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: 'x.com.samsung.da.cumulativeSavedPower' in rep),
        # Monthly billing-cycle totals -- the completed prior month and the
        # in-progress current month. Not ever-increasing (each resets at
        # month boundary), so no state_class.
        SensorDesc(key='energy_last_month_kwh', field='x.com.samsung.da.monthlyConsumption',
                   name='Energy (last month)', device_class='energy',
                   unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: 'x.com.samsung.da.monthlyConsumption' in rep),
        SensorDesc(key='energy_this_month_kwh', field='x.com.samsung.da.thismonthlyConsumption',
                   name='Energy (this month)', device_class='energy',
                   unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: 'x.com.samsung.da.thismonthlyConsumption' in rep),
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

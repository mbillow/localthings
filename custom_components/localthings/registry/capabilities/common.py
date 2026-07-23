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
from ..entities import (
    BinarySensorDesc, ButtonDesc, SelectDesc, SensorDesc, SwitchDesc,
)


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


def sensor_item_value(items, sensor_type, index=0):
    """Pull one reading out of a `/sensors/vs/0`-style items[] list -- each
    item is `{type, value: [...]}`; `index` picks which slot of a possibly
    multi-value reading to read (index 0 is the raw measurement on every
    family seen so far). Shared by range_hood.AIR_QUALITY and
    air_purifier.AIR_QUALITY, which read the same resource shape."""
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if item.get('x.com.samsung.da.type') != sensor_type:
            continue
        values = item.get('x.com.samsung.da.value') or ()
        if index < len(values):
            try:
                return int(values[index])
            except (TypeError, ValueError):
                return None
    return None


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


def remote_control_enabled(resources: dict) -> bool:
    """Single source of truth for the /remotectrl on/off signal, mirroring
    REMOTE_CONTROL_GENERIC/_VS_FALLBACK's href/field pair and precedence
    below. Used both to render the read-only Smart Control binary_sensor
    (via those two descriptors) and, from coordinator.async_send_command,
    to block writes outright when remote control is off. Both hrefs are
    poll_tier='warm' below so that gate reads recent state (subscribed
    when observe is live, subpolled every ~6s otherwise) rather than a
    once-per-30s cold summary poll. True (assume enabled) when neither
    href is present -- most device types don't report this capability
    at all."""
    generic = resources.get('/remotectrl/0')
    if generic is not None:
        return bool(generic.get('value'))
    fallback = resources.get('/remotectrl/vs/0')
    if fallback is not None:
        return str(fallback.get('x.com.samsung.da.remoteControlEnabled')).lower() == 'true'
    return True


REMOTE_CONTROL_GENERIC = Capability(
    href='/remotectrl/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(key='remote_control', field='value',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: bool(v)),
    ),
)

REMOTE_CONTROL_VS_FALLBACK = Capability(
    href='/remotectrl/vs/0',
    match_fn=lambda rep, resources: '/remotectrl/0' not in resources,
    poll_tier='warm',
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
        # both. Self-gates off where only cumulativePower is present. `not
        # rep or` keeps the same empty-{} stub carve-out as power_watts/
        # energy_kwh above -- without it, an exists_fn permanently drops the
        # entity if setup happens to land on a not-yet-fetched stub.
        SensorDesc(key='power_energy_kwh', field='x.com.samsung.da.cumulativeConsumption',
                   name='Power energy', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.cumulativeConsumption' in rep)),
        # AI Energy Mode's lifetime savings estimate vs. an unoptimized
        # baseline -- present on some models (e.g. TP1X_REF_21K, issue #21/
        # #27) and absent on others (issue #20/#26), unlike cumulativePower.
        SensorDesc(key='energy_saved_kwh', field='x.com.samsung.da.cumulativeSavedPower',
                   name='Energy saved', device_class='energy',
                   state_class='total_increasing', unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.cumulativeSavedPower' in rep)),
        # Monthly billing-cycle totals -- the completed prior month and the
        # in-progress current month. Not ever-increasing (each resets at
        # month boundary), so no state_class.
        SensorDesc(key='energy_last_month_kwh', field='x.com.samsung.da.monthlyConsumption',
                   name='Energy (last month)', device_class='energy',
                   unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.monthlyConsumption' in rep)),
        SensorDesc(key='energy_this_month_kwh', field='x.com.samsung.da.thismonthlyConsumption',
                   name='Energy (this month)', device_class='energy',
                   unit='kWh', value_fn=wh_to_kwh,
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.thismonthlyConsumption' in rep)),
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

# AI energy-saving level -- '0' is off, and supportedAiLevel lists the
# additional level(s) the device offers ('1' meaning just "on" on most
# hardware, but multi-level boards have been reported). Verified cross-family:
# fridge (issue #21) and washer (issue #40) both expose this href.
#
# supportedAiLevel is a single-entry list on most captured hardware, where a
# select would offer only one real choice against an implicit "off" -- shown
# as a switch instead. '0' itself is never in supportedAiLevel but has been
# observed live as the off value of aiLevel, so the select synthesizes it
# back in as an explicit option rather than leaving no way to turn off.
#
# No translation_key: aiLevel's values are plain digit strings, and
# select.py's _display() already renders an untranslated numeric string
# as-is -- there's nothing a strings.json entry adds that's worth maintaining
# against an unknown, growing number of future levels.


def _ai_energy_supported_levels(rep):
    """supportedAiLevel as a list -- a stray scalar (e.g. a string) must not
    be len()-checked as if it were a list."""
    sl = rep.get('supportedAiLevel')
    return list(sl) if isinstance(sl, (list, tuple)) else []


def _ai_energy_level_options(resources):
    rep = resources.get('/energy/ailevel/vs/0') or {}
    return ['0', *_ai_energy_supported_levels(rep)]


def _ai_energy_level_write(p, rep, href=None):
    return ['energy', 'ailevel', 'vs', '0'], {'aiLevel': p}


def _ai_energy_level_switch_write(p, rep, href=None):
    levels = _ai_energy_supported_levels(rep)
    on_level = levels[0] if levels else '1'
    return ['energy', 'ailevel', 'vs', '0'], {'aiLevel': on_level if p == 'On' else '0'}


AI_ENERGY_LEVEL = Capability(
    href='/energy/ailevel/vs/0',
    poll_tier='cold',
    entities=(
        # No `not rep` stub carve-out on either side, unlike most exists_fn
        # gates in this file -- entity creation only ever runs once, against
        # whichever snapshot happens to be current the moment platforms are
        # set up (see entity._is_included / __init__.py's
        # async_config_entry_first_refresh-before-forward-entry-setups
        # ordering), while flatten() re-evaluates exists_fn every poll
        # against live data. Both descriptors share key='ai_energy_level',
        # so if a stub carve-out let one of them win at setup time while the
        # other wins once real data lands, flatten() would feed the
        # instantiated entity a value shaped for the other platform (e.g. a
        # bool into a Select). Requiring real, populated data on both sides
        # keeps the entity-creation decision and the live-value decision in
        # permanent agreement -- the cost is this entity doesn't appear
        # until a reload if the device's very first poll stubs this
        # cold-tier href, the same reload already required to fix which
        # platform got picked in that case.
        SwitchDesc(key='ai_energy_level', field='aiLevel',
                   name='AI energy level', icon='mdi:leaf',
                   entity_category='config',
                   value_fn=lambda v: v != '0',
                   exists_fn=lambda rep, resources: (
                       len(_ai_energy_supported_levels(rep)) == 1),
                   write_fn=_ai_energy_level_switch_write),
        SelectDesc(key='ai_energy_level', field='aiLevel',
                   name='AI energy level', icon='mdi:leaf',
                   entity_category='config',
                   options=_ai_energy_level_options,
                   exists_fn=lambda rep, resources: (
                       len(_ai_energy_supported_levels(rep)) > 1),
                   write_fn=_ai_energy_level_write),
    ),
)

FIRMWARE_UPDATE = Capability(
    href='/otninformation/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(
            key='firmware_update',
            field='x.com.samsung.da.newVersionAvailable',
            name='Firmware update available',
            device_class='update',
            entity_category='diagnostic',
            value_fn=lambda v: str(v).lower() == 'true' if v is not None else None,
        ),
    ),
)

SELF_CHECK = Capability(
    href='/selfcheck/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='selfcheck_status', field='x.com.samsung.da.status',
                   name='Self-check status', icon='mdi:stethoscope',
                   entity_category='diagnostic'),
        SensorDesc(key='selfcheck_result', field='x.com.samsung.da.result',
                   name='Self-check result', icon='mdi:clipboard-check-outline',
                   entity_category='diagnostic'),
        # List of error codes from the last self-check; joined for display.
        # Not every fridge reports the field, hence the exists_fn.
        SensorDesc(key='selfcheck_error', field='x.com.samsung.da.error',
                   name='Self-check error', icon='mdi:alert-circle-outline',
                   entity_category='diagnostic',
                   exists_fn=lambda rep, resources: (
                       not rep or 'x.com.samsung.da.error' in rep),
                   value_fn=lambda v: (', '.join(v) if v else None) if isinstance(v, list) else v),
        ButtonDesc(key='selfcheck_start', field='', name='Start self-check',
                   payload='Start', icon='mdi:play-circle-outline',
                   entity_category='diagnostic',
                   write_fn=lambda p, rep, href=None: (
                       ['selfcheck', 'vs', '0'], {'x.com.samsung.da.status': p})),
    ),
)

# ---------------------------------------------------------------------------
# Cross-family bundles, unpacked into every by_type registry's _build([...])
# call the same way ignored.IGNORED is (*common.UNIVERSAL / *common.POWER).
# discover() only binds a capability whose href is actually present in a
# given device's resource dump, so listing one here for a family that
# doesn't expose the href is a no-op, not a phantom entity -- see the
# adding-device-support skill's coverage-discipline section.
#
# UNIVERSAL holds every capability with no known family that both (a) has
# the href and (b) needs to model it some other way -- broadening one of
# these to a new family is a safe, harmless guess (issue #40's AI energy
# level: 2 of 6 families confirmed, blanket-added everywhere else).
#
# POWER is kept separate -- airconditioner is the one family that opts out
# of it. Canonical reason (see by_type/airconditioner.py and its test for
# pointers back here, not restatements): AC's climate entity already owns
# /power/0 and /power/vs/0 via bare, no-entity Capability objects
# (airconditioner.COVERAGE), and a second, real POWER_GENERIC/
# POWER_VS_FALLBACK cap on the same href would make _build() raise (a href
# with >1 cap must have every cap discriminated by rt_filter/match_fn, and
# the bare COVERAGE cap has neither). Kids-lock/remote-control don't have
# this conflict -- no AC dump has ever reported those hrefs -- so they stay
# in UNIVERSAL.
# ---------------------------------------------------------------------------

UNIVERSAL = (
    ALARMS,
    ENERGY_METER,
    FIRMWARE_UPDATE,
    SELF_CHECK,
    AI_ENERGY_LEVEL,
    KIDS_LOCK_GENERIC,
    KIDS_LOCK_VS_FALLBACK,
    REMOTE_CONTROL_GENERIC,
    REMOTE_CONTROL_VS_FALLBACK,
)

POWER = (
    POWER_GENERIC,
    POWER_VS_FALLBACK,
)

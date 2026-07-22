"""Capabilities for the Samsung air-conditioner family (ARTIK051_PRAC-class).

Resources verified against the issue #17 diagnostics dump (model
ARTIK051_PRAC_20K). This is the first family whose core controls surface as a
single composite HA `climate` entity rather than a scatter of switches/selects:
power (on/off), HVAC mode, current/target temperature, fan (wind) strength,
swing (wind direction), and the convenient-mode preset all live on one climate
card. The climate platform (climate.py) reads those sibling resources from the
coordinator snapshot; here we bind the primary `/mode/vs/0` resource to the
`ClimateDesc` and mark the consumed siblings as covered.

None of these caps may go into the global `ALL`/`CAPABILITIES`: `/mode/vs/0`,
`/temperatures/vs/0`, `/humidity/*` collide with fridge/oven hrefs of a
different schema (see capabilities/__init__.py). They live only in the AC
by_type registry.
"""
from ..capability import Capability
from ..entities import ClimateDesc, SensorDesc, SwitchDesc

# ---------------------------------------------------------------------------
# Canonical AC resource hrefs. The climate entity (climate.py) binds the
# primary HREF_MODE via CLIMATE below and reads the CLIMATE_CONSUMED_HREFS
# siblings off the coordinator snapshot; those siblings are marked covered
# (no-entity caps) so discover() reports no gap. Declared once here and
# imported by climate.py, so a new sibling read can't drift out of sync with
# its coverage entry.
# ---------------------------------------------------------------------------
HREF_MODE = '/mode/vs/0'                          # primary (bound by CLIMATE)
HREF_POWER = '/power/0'                           # on/off -> HVACMode.OFF / TURN_ON/OFF
HREF_POWER_VS = '/power/vs/0'                     # vendor fallback for on/off
HREF_TEMP_CURRENT = '/temperature/current/0'      # current_temperature
HREF_TEMP_DESIRED = '/temperature/desired/0'      # target_temperature (write target)
HREF_TEMP_CONTROL = '/temperature/control/vs/0'   # target_temperature_step
HREF_WIND_STRENGTH = '/wind/strength/vs/0'        # fan_mode
HREF_WIND_DIRECTION = '/wind/direction/vs/0'      # swing_mode
HREF_CONVENIENT = '/mode/convenient/vs/0'         # preset_mode

CLIMATE_CONSUMED_HREFS = [
    HREF_POWER, HREF_POWER_VS, HREF_TEMP_CURRENT, HREF_TEMP_DESIRED,
    HREF_TEMP_CONTROL, HREF_WIND_STRENGTH, HREF_WIND_DIRECTION, HREF_CONVENIENT,
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _filter_usage_percent(rep):
    """Filter usage as a percentage of rated capacity. The device reports
    `filterUsage` as a raw count in `filterCapacityUnit` (Hours here, e.g.
    100 of a 500 capacity), so a plain value with a '%' unit would be wrong --
    normalize to used/capacity. Returns None when capacity is missing/zero."""
    used = _num(rep.get('x.com.samsung.da.filterUsage'))
    cap = _num(rep.get('x.com.samsung.da.filterCapacity'))
    if used is None or not cap:
        return None
    return round(used / cap * 100)


def _first_mode(rep):
    """Representative scalar for the climate entity in the flattened state
    (golden/regression). The real entity computes hvac_mode from power + mode."""
    modes = rep.get('x.com.samsung.da.modes')
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


def _climate_write(payload, rep, href=None):
    """Map a (kind, value) command from the climate platform to the
    (path_segs, body) for that one sub-write. `value` is already the raw device
    code (the platform maps HA<->device). async_send_command POSTs to path_segs,
    so a single desc drives writes across power/mode/temperature/wind resources.
    Read-modify-write safe: each write sends only its own field, leaving the
    resource's other fields (e.g. /mode/vs/0's opaque `options` blob) untouched.
    """
    kind, value = payload
    if kind == 'power':
        return (['power', '0'], {'value': bool(value)})
    if kind == 'mode':
        return (['mode', 'vs', '0'], {'x.com.samsung.da.modes': [value]})
    if kind == 'temperature':
        return (['temperature', 'desired', '0'], {'temperature': int(round(float(value)))})
    if kind == 'fan':
        return (['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': value})
    if kind == 'swing':
        return (['wind', 'direction', 'vs', '0'], {'x.com.samsung.da.modes': value})
    if kind == 'preset':
        return (['mode', 'convenient', 'vs', '0'], {'x.com.samsung.da.modes': value})
    return None


CLIMATE = Capability(
    href=HREF_MODE,
    poll_tier='warm',
    entities=(
        ClimateDesc(key='climate', translation_key='airconditioner',
                    rep_fn=_first_mode, write_fn=_climate_write),
    ),
)

AIR_PURIFY = Capability(
    href='/option/airpurify/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='air_purify', field='x.com.samsung.da.modes',
                   name='Air purification', icon='mdi:air-purifier',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'airpurify', 'vs', '0'],
                       {'x.com.samsung.da.modes': 'On' if p == 'On' else 'Off'})),
    ),
)

AUTO_CLEAN = Capability(
    href='/option/autoclean/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='auto_clean', field='x.com.samsung.da.settingStatus',
                   name='Auto clean', icon='mdi:spray-bottle',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'autoclean', 'vs', '0'],
                       {'x.com.samsung.da.settingStatus': 'On' if p == 'On' else 'Off'})),
    ),
)

AIR_FILTER = Capability(
    href='/filter/airdustfilter/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='air_filter_usage', rep_fn=_filter_usage_percent,
                   name='Filter usage', unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic'),
        SensorDesc(key='air_filter_status', field='x.com.samsung.da.filterStatus',
                   name='Filter status', device_class='enum',
                   options=('normal', 'wash', 'replace'),
                   translation_key='air_filter_status',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

# ---------------------------------------------------------------------------
# AC-scoped coverage: the CLIMATE_CONSUMED_HREFS above (read by the climate
# entity) plus vendor duplicates / all-zero-ambiguous / plumbing resources.
# These are NOT in the global ignored.IGNORED because several of them
# (/mode/vs/0 handled above, /temperatures/vs/0, /humidity/*) collide with
# other families' schemas. A no-entity Capability still marks the href as
# bound so discover() reports no coverage gap.
# ---------------------------------------------------------------------------
_AC_IGNORED = [
    # Vendor superset that duplicates the OCF /temperature/current+desired pair.
    '/temperatures/vs/0',
    # All-zero and ambiguously encoded on this model (2-value arrays); the
    # 'don't guess' rule -- leave unmodeled rather than invent entities.
    '/sensors/vs/0',
    '/humidity/0',
    '/humidity/vs/0',
    # Presence-personalization plumbing (empty item list here).
    '/personality/presence/vs/0',
]

# Built as bare no-entity caps; folded into the AC registry (not global).
COVERAGE = [Capability(href=h) for h in (CLIMATE_CONSUMED_HREFS + _AC_IGNORED)]

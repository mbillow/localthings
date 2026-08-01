"""Capabilities for the Samsung EHS (Eco Heating System) air-to-water heat
pump family (TP1X_DA_AC_EHS-class, model TP1X_DA_AC_EHS_01001_0000).

An EHS unit runs two independently-controlled loops off one outdoor unit:
space heating/cooling ("zone1", through /mode/vs/0, /power/vs/0,
/temperatures/indoor/vs/0) and domestic hot water ("dhw", through
/mode/dhw/vs/0, /power/dhw/vs/0, /temperatures/dhw/vs/0). There's no shared
vocabulary with the room-AC family in airconditioner.py beyond the DA_AC_
board prefix -- EHS reports its own /mode/*/vs/0 and /temperatures/*/vs/0
shapes, not airconditioner.py's HREF_MODE/HREF_TEMP* OCF-pattern hrefs, and
this integration has no `water_heater` platform yet, so both loops are
exposed as switch/select/number/sensor rather than a composite climate/
water_heater card -- same shape as dehumidifier.py's power/mode/humidity
split, just with two loops instead of one.

Verified against a real TP1X_DA_AC_EHS_01001_0000 diagnostics dump
(firmware AEH-WW-TP1-22-AE6000_17260402, TizenRT 3.1 / DAWIT 2.0).
"""
from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import normalize_temp_unit


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_mode(rep):
    """Representative scalar for a mode select -- `modes` is a single-element
    list on every dump seen so far, mirroring airconditioner._first_mode /
    dehumidifier._first_mode's handling of the same field shape."""
    modes = rep.get('x.com.samsung.da.modes')
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


def _temp_unit(rep):
    return normalize_temp_unit(rep.get('x.com.samsung.da.unit'), '°C')


ZONE_POWER = Capability(
    href='/power/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='zone_power', field='x.com.samsung.da.power',
                   icon='mdi:radiator',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['power', 'vs', '0'],
                       {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

ZONE_MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='zone_mode', rep_fn=_first_mode,
                   icon='mdi:sun-snowflake-variant',
                   options_field='x.com.samsung.da.supportedModes',
                   write_fn=lambda p, rep, href=None: (
                       ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [p]})),
    ),
)

# type=Water/unit=Celsius on this dump names the space-heating loop's flow/
# room setpoint, not a literal water temperature -- Samsung EHS zone control
# is leaving-water-temperature-based, same convention as the dhw loop below.
ZONE_TEMPERATURE = Capability(
    href='/temperatures/indoor/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='zone_temperature', field='x.com.samsung.da.current',
                   device_class='temperature', unit_fn=_temp_unit,
                   state_class='measurement', value_fn=_num),
        NumberDesc(key='zone_target_temperature', field='x.com.samsung.da.desired',
                   device_class='temperature', unit_fn=_temp_unit,
                   entity_category='config', value_fn=_num,
                   native_min_fn=lambda rep: _num(rep.get('x.com.samsung.da.minimum')) or 5.0,
                   native_max_fn=lambda rep: _num(rep.get('x.com.samsung.da.maximum')) or 30.0,
                   step_fn=lambda rep: _num(rep.get('x.com.samsung.da.increment')) or 0.5,
                   write_fn=lambda p, rep, href=None: (
                       ['temperatures', 'indoor', 'vs', '0'],
                       {'x.com.samsung.da.desired': str(float(p))})),
    ),
)

DHW_POWER = Capability(
    href='/power/dhw/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='dhw_power', field='x.com.samsung.da.power',
                   icon='mdi:water-boiler',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['power', 'dhw', 'vs', '0'],
                       {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

DHW_MODE = Capability(
    href='/mode/dhw/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='dhw_mode', rep_fn=_first_mode,
                   icon='mdi:water-thermometer',
                   options_field='x.com.samsung.da.supportedModes',
                   write_fn=lambda p, rep, href=None: (
                       ['mode', 'dhw', 'vs', '0'], {'x.com.samsung.da.modes': [p]})),
    ),
)

DHW_TEMPERATURE = Capability(
    href='/temperatures/dhw/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='dhw_temperature', field='x.com.samsung.da.current',
                   device_class='temperature', unit_fn=_temp_unit,
                   state_class='measurement', value_fn=_num),
        NumberDesc(key='dhw_target_temperature', field='x.com.samsung.da.desired',
                   device_class='temperature', unit_fn=_temp_unit,
                   entity_category='config', value_fn=_num,
                   native_min_fn=lambda rep: _num(rep.get('x.com.samsung.da.minimum')) or 40.0,
                   native_max_fn=lambda rep: _num(rep.get('x.com.samsung.da.maximum')) or 70.0,
                   step_fn=lambda rep: _num(rep.get('x.com.samsung.da.increment')) or 0.5,
                   write_fn=lambda p, rep, href=None: (
                       ['temperatures', 'dhw', 'vs', '0'],
                       {'x.com.samsung.da.desired': str(float(p))})),
    ),
)

AWAY_MODE = Capability(
    href='/option/outgoing/vs/0',
    poll_tier='cold',
    entities=(
        SwitchDesc(key='away_mode', field='x.com.samsung.da.away',
                   icon='mdi:home-export-outline',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'outgoing', 'vs', '0'],
                       {'x.com.samsung.da.away': 'On' if p == 'On' else 'Off'})),
    ),
)

# ---------------------------------------------------------------------------
# EHS-scoped coverage: opaque vendor plumbing (hex-encoded factory/cycle/
# schedule blobs) or resources with no confirmed write contract on this
# dump, following the same 'don't guess' rule as dehumidifier._DHM_IGNORED.
# Not in the global ignored.IGNORED since these are EHS-only shapes that
# would need their own verification on other device families.
# ---------------------------------------------------------------------------
_EHS_IGNORED = [
    '/availablecontrolsets/vs/0',  # opaque hex-encoded control-set bitmap (id: EHS)
    '/da/softreset/vs/0',          # soft-reset trigger plumbing
    '/diagnosis/vs/0',             # empty {} on this dump
    '/ehscycle/vs/0',              # opaque hex-encoded indoor/outdoor cycle log
    '/ehsfsv/vs/0',                # opaque hex-encoded factory setting values
    '/option/dhwdisplay/vs/0',     # front-panel DHW-display show/hide, cosmetic only
    '/reserverulesets/vs/0',       # opaque hex-encoded schedule reservation blob
    '/sac/installationinfo/vs/0',  # static outdoor/indoor installation info, diagnostic only
    '/actions/zone1/vs/0',         # zone1 schedule/timer program -- unmodeled for now
    '/actions/dhw/vs/0',           # DHW schedule/timer program -- unmodeled for now
]

COVERAGE = [Capability(href=h) for h in _EHS_IGNORED]

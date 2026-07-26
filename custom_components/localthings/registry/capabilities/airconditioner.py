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
from ..entities import BinarySensorDesc, ClimateDesc, NumberDesc, SensorDesc, SwitchDesc
from .laundry import bool_option_exists, bool_option_value, option_value, option_write

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
HREF_TEMPS_VS = '/temperatures/vs/0'              # vendor temp fallback (items[] array)
HREF_LIGHT = '/light/vs/0'                        # display light (absent on TP2X)

CLIMATE_CONSUMED_HREFS = [
    HREF_POWER, HREF_POWER_VS, HREF_TEMP_CURRENT, HREF_TEMP_DESIRED,
    HREF_TEMP_CONTROL, HREF_TEMPS_VS, HREF_WIND_STRENGTH, HREF_WIND_DIRECTION,
    HREF_CONVENIENT,
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- /mode/vs/0 options blob ('Light_On', 'Volume_100'/'Volume_Mute', ...) ---
# Some settings (display light, beep volume) have no dedicated resource on
# models like TP2X_RAC_20K; they live only in /mode/vs/0's `options` array and
# are written back one token at a time (the device merges by prefix -- see
# common.merge_options_field), exactly as air_purifier.MODE does for its own
# Light token. Reuses laundry's option helpers rather than re-deriving them.
def _volume_percent(rep):
    """Beep volume as 0-100, with the 'Mute' token folded in as 0."""
    v = option_value(rep.get('x.com.samsung.da.options'), 'Volume')
    if v is None:
        return None
    return 0 if v == 'Mute' else _num(v)


def _volume_write(payload, rep, href=None):
    pct = int(round(float(payload)))
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write(
            'Volume', 'Mute' if pct <= 0 else pct),
    }


def _light_write(payload, rep, href=None):
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write(
            'Light', 'On' if payload else 'Off'),
    }


def _humidity_percent(rep):
    """Relative humidity, or None where neither field carries a real reading.

    /humidity/vs/0 exposes two fields and which one is live varies by board.
    A TP2X_RAC_20K measured in the field reports both x.com.samsung.da.humidity
    and .fivepercentHumidity as the same percentage ('51'), which is what
    establishes they are one quantity; every dump this family has been
    verified against instead pins .humidity to a dead '0'/'0.000000' sentinel
    and carries the reading only in .fivepercentHumidity (43, 83, 53). So:
    prefer the primary field, fall back to the five-percent one, and gate the
    sensor off when neither is plausible -- 0 % RH is the sentinel, never a
    real room. That last part is why this href had sat in _AC_IGNORED.
    """
    for field in ('x.com.samsung.da.humidity',
                  'x.com.samsung.da.fivepercentHumidity'):
        v = rep.get(field)
        if isinstance(v, (list, tuple)):
            continue
        n = _num(v)
        if n is not None and 0 < n <= 100:
            return n
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


def _climate_write(payload, rep, href=None, resources=None):
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
        # Two setpoint layouts exist across models: the scalar
        # /temperature/desired/0, and the vendor /temperatures/vs/0 items
        # array (e.g. TP2X_RAC_20K, which returns 4.04 Not Found on
        # /temperature/desired/0 — see current_/target_temperature's read
        # fallback above). Prefer the scalar when the device exposes it; else
        # write the vendor items array. `resources` is the device snapshot
        # (keyed by href); when absent (legacy caller) keep the scalar path.
        if resources is not None and '/temperature/desired/0' not in resources:
            return (['temperatures', 'vs', '0'],
                    {'x.com.samsung.da.items': [
                        {'x.com.samsung.da.id': '0',
                         'x.com.samsung.da.desired': f"{float(value):.1f}"}]})
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
        # Display light + beep volume: on models like TP2X_RAC_20K these are
        # options on /mode/vs/0 -- there is no /light/vs/0 (4.04 there) and no
        # dedicated volume resource at all -- so bind them here and gate on the
        # token actually being reported. display_light additionally stands down
        # where DISPLAY_LIGHT's own /light/vs/0 exists, so a model carrying both
        # gets one switch from the dedicated resource rather than two under the
        # same key.
        SwitchDesc(key='display_light', icon='mdi:led-on',
                   entity_category='config',
                   rep_fn=bool_option_value('Light'),
                   exists_fn=lambda rep, resources: (
                       HREF_LIGHT not in resources
                       and bool_option_exists('Light')(rep, resources)),
                   write_fn=_light_write),
        NumberDesc(key='sound_volume', icon='mdi:volume-high',
                   entity_category='config',
                   native_min=0, native_max=100, step=1,
                   rep_fn=_volume_percent,
                   exists_fn=lambda rep, resources: (
                       option_value(rep.get('x.com.samsung.da.options'),
                                    'Volume') is not None),
                   write_fn=_volume_write),
    ),
)

# 'warm' rather than the default 'cold': a cold href is only refreshed by the
# ~30s /device/0 summary sweep, and this resource comes back as a stub there on
# TP2X -- so a cold humidity sensor would sit at unknown indefinitely.
HUMIDITY = Capability(
    href='/humidity/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='humidity', device_class='humidity', unit='%',
                   state_class='measurement', rep_fn=_humidity_percent,
                   exists_fn=lambda rep, resources: (
                       not rep or _humidity_percent(rep) is not None)),
    ),
)

AIR_PURIFY = Capability(
    href='/option/airpurify/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='air_purify', field='x.com.samsung.da.modes',
                   icon='mdi:air-purifier',
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
                   icon='mdi:spray-bottle',
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
                   unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic'),
        SensorDesc(key='air_filter_status', field='x.com.samsung.da.filterStatus',
                   device_class='enum',
                   options=('normal', 'wash', 'replace'),
                   translation_key='filter_status',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
    ),
)

DISPLAY_LIGHT = Capability(
    href=HREF_LIGHT,
    poll_tier='cold',
    entities=(
        SwitchDesc(key='display_light', field='mode',
                   icon='mdi:led-on',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['light', 'vs', '0'],
                       {'mode': 'On' if p == 'On' else 'Off'})),
    ),
)

# Confirmed against issue #38's dump (TP1X_DA-AC-RAC-01001_0000): a single
# boolean field, no vendor prefix, mirroring the On/Off convention used
# throughout the rest of this API.
MUTE_ONCE = Capability(
    href='/option/muteonce/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='mute_once', field='muteonce',
                   icon='mdi:volume-mute',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['option', 'muteonce', 'vs', '0'],
                       {'muteonce': 'On' if p == 'On' else 'Off'})),
    ),
)

# Circuit-breaker current-limit setting (issue #38, TP1X board): `operation`
# toggles the limiter and `modes` picks a level out of `supportedModes`
# (seen as '3'..'9'). No vendor field-name prefix and no unit/label in the
# dump to confirm what the levels mean (amps vs. an abstract tier) --
# exposed read-only per the 'don't guess' rule rather than risking an
# unverified write to live HVAC hardware.
CURRENT_LIMIT = Capability(
    href='/electriccurrent/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(key='current_limit_enabled', field='operation',
                          icon='mdi:current-ac',
                          entity_category='diagnostic',
                          value_fn=lambda v: v == 'On'),
        SensorDesc(key='current_limit_level', field='modes',
                   icon='mdi:current-ac',
                   entity_category='diagnostic'),
    ),
)

# ---------------------------------------------------------------------------
# AC-scoped coverage: the CLIMATE_CONSUMED_HREFS above (read by the climate
# entity) plus vendor duplicates / all-zero-ambiguous / plumbing resources.
# These are NOT in the global ignored.IGNORED because several of them
# (/mode/vs/0 handled above, /temperatures/vs/0, /humidity/*) collide with
# other families' schemas. A no-entity Capability still marks the href as
# bound so discover() reports no coverage gap.
#
# CLIMATE_CONSUMED_HREFS carry the climate card's actual displayed state
# (power, current/target temp, fan, swing, preset) -- the coordinator only
# OBSERVE-subscribes and sub-polls 'hot'/'warm' hrefs (see coordinator.py),
# so leaving these at the Capability default of 'cold' meant every state
# change was invisible until the next full /device/0 summary sweep
# (~30s -- issue #17: instant device response, 20-30s HA lag). Pin them to
# 'warm' -- same tier as CLIMATE's own primary href -- so they get push
# notifications (or, in poll-only mode, the warm sub-poll cadence) instead
# of waiting on the summary sweep.
# ---------------------------------------------------------------------------
_AC_IGNORED = [
    # All-zero and ambiguously encoded on this model (2-value arrays); the
    # 'don't guess' rule -- leave unmodeled rather than invent entities.
    # /humidity/vs/0 used to sit here for the same reason; it is now modeled by
    # HUMIDITY above, which self-gates back off on exactly that encoding, so it
    # must NOT be listed here as well (two undiscriminated caps on one href is
    # a _build error). /humidity/0 stays -- 4.04 on TP2X, empty elsewhere.
    '/sensors/vs/0',
    '/humidity/0',
    # Presence-personalization plumbing (empty item list here).
    '/personality/presence/vs/0',
    # --- TP1X/TP2X-class housekeeping / opaque blobs. These carry no
    # user-actionable state or no documented write contract, so per the
    # 'don't guess' rule they are ignored rather than modeled.
    # /option/muteonce/vs/0 and /selfcheck/vs/0 are deliberately NOT here --
    # see MUTE_ONCE above and common.SELF_CHECK (via common.UNIVERSAL) in
    # the by_type registry, both of which have a confirmed, cleanly
    # modelable contract.
    '/airlevelcheck/vs/0',         # periodic air-quality sensing scheduler plumbing
    '/aisleep/vs/0',               # AI-sleep feedback state (no actionable control)
    '/availablecontrolsets/vs/0',  # opaque hex-encoded control-set bitmap
    '/da/softreset/vs/0',          # soft-reset trigger plumbing
    '/keepnormalstate/vs/0',       # internal keep-normal flag
    '/mds/absencemonitoring/vs/0', # motion-detection sensor plumbing (empty here)
    '/mds/absencestate/vs/0',      # motion-detection state (empty here)
    '/remotedatacontrol/vs/0',     # remote data-control session status
    '/remotetemperature/vs/0',     # external temp-sensor feed (unset on this unit)
    '/reserverulesets/vs/0',       # opaque hex-encoded schedule reservation blob
    '/welcome/temperature/vs/0',   # welcome-cooling plumbing
    # System-AC-only (multi-indoor-unit commercial installs, e.g.
    # A-CAWW-TP2-20-COMMON, issue #52): opaque hex-encoded installation
    # topology -- indoor/outdoor unit pairing, per-unit serials, MCU info.
    # Commissioning-time plumbing, not user-actionable appliance state.
    '/sac/installationinfo/vs/0',
]

# Built as bare no-entity caps; folded into the AC registry (not global).
COVERAGE = [
    Capability(href=h, poll_tier='warm') for h in CLIMATE_CONSUMED_HREFS
] + [
    Capability(href=h) for h in _AC_IGNORED
]

"""Capabilities for the Samsung ARTIK051_TVTL-class air purifier family
(model AX60R5080WD/SE, issue #56).

Power, kids-lock, remote-control, alarms, and the energy meter are the shared
common.py capabilities (this family exposes the standard /power/0+/power/vs/0
pair and /alarms/vs/0, /energy/consumption/vs/0). /diagnosis/vs/0 reuses
dishwasher.DIAGNOSIS -- identical field/write contract
(x.com.samsung.da.diagnosisStart, 'Ready' on both dumps).

/mode/vs/0's x.com.samsung.da.options array packs multiple independent
'<Prefix>_<value>' flags into one list -- the same packed-list contract
laundry.py's option_value/option_write already model for /course/vs/0's
options[] (reused directly below, just against this family's own href). Per
issue #56's follow-up (five diagnostics dumps captured with the physical unit
set to Auto/Sleep/Low/Medium/High):
  Light_On / Light_Off  -- a plain on/off flag; MODE below models it as a
                            real switch, RMW-replacing just that one entry.
  Comode_Off            -- read 'Off' on *every* one of the five dumps,
                            including High/Low/Medium/Auto -- confirms this
                            is NOT the fan-speed selector (ruling out the
                            original guess); exposed read-only since its
                            actual purpose is still unconfirmed.
  OptionCode_60282       -- confirmed opaque/not user-facing in the
                            SmartThings app; not modeled (same treatment as
                            range_hood's OptionCode_* token on the same
                            href).
  Blooming_*             -- confirmed to have no corresponding SmartThings
                            app setting; dropped entirely rather than kept
                            as an unexplained diagnostic (it did track 1:1
                            with Sleep mode across the five dumps -- 0 in
                            Sleep, 6 otherwise -- so it's plausibly an
                            automatic side effect of sleep mode, e.g. a
                            display-dimming level, but that's still a guess).

/airflow/0 and /airflow/vs/0's `speed` still isn't modeled as a real
fan-speed control: across the same five dumps it read 0 for both Auto *and*
High, and 3 for Low/Medium *and* Sleep -- not a monotonic mapping to any
selectable level, and the dumps were all captured within about three minutes
of each other (only one poll cycle apart at this integration's 30s summary
interval), so the values may not have settled after each change before the
diagnostics snapshot was taken. Exposed read-only pending a confirmed,
stable capture -- see the issue #56 discussion for what's needed.
"""
import datetime

from ..capability import Capability
from ..entities import (
    BinarySensorDesc, ButtonDesc, FanDesc, NumberDesc, SelectDesc, SensorDesc,
    SwitchDesc, TimeDesc,
)
from .airconditioner import _filter_usage_percent
from .common import int_or_none, sensor_item_value
from .laundry import bool_option_exists, bool_option_value, option_value, option_write
from .range_hood import _timestamp


def _quality(sensor_type):
    return lambda items: sensor_item_value(items, sensor_type)


# The particulate readings (index 0 of each items[] entry) are numeric µg/m³
# measurements, surfaced as HA's pm10/pm2.5/pm1.0 device classes so they graph
# and carry a unit; odour and clean-level are unitless indices (measurement).
AIR_QUALITY = Capability(
    href='/sensors/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='dust', field='x.com.samsung.da.items',
                   device_class='pm10', unit='µg/m³', state_class='measurement',
                   icon='mdi:blur', value_fn=_quality('Dust')),
        SensorDesc(key='fine_dust', field='x.com.samsung.da.items',
                   device_class='pm25', unit='µg/m³', state_class='measurement',
                   icon='mdi:blur', value_fn=_quality('FineDust')),
        SensorDesc(key='super_fine_dust', field='x.com.samsung.da.items',
                   device_class='pm1', unit='µg/m³', state_class='measurement',
                   icon='mdi:blur', value_fn=_quality('SuperFineDust')),
        SensorDesc(key='odor', field='x.com.samsung.da.items',
                   state_class='measurement', icon='mdi:scent',
                   value_fn=_quality('Odor')),
        # CleanLevel is the device's overall air-quality index (CAQI). Named
        # 'Air quality' via translation_key (leaving range_hood's shared
        # 'clean_level' catalog entry alone).
        SensorDesc(key='clean_level', field='x.com.samsung.da.items',
                   translation_key='air_quality', unit='CAQI',
                   state_class='measurement', icon='mdi:weather-hazy',
                   value_fn=_quality('CleanLevel')),
    ),
)


def _consumable_state(items, name):
    """Read a `/consumable/vs/0`-style items[] entry -- {name, state} pairs,
    unlike AIR_QUALITY's {type, value} shape above."""
    for item in items or ():
        if isinstance(item, dict) and item.get('x.com.samsung.da.name') == name:
            return item.get('x.com.samsung.da.state')
    return None


# FilterProgress is a 0-100 percentage counting up as the filter wears --
# confirmed via issue #56: the SmartThings app shows "Filter needs changing"
# once this reaches 100, so 100 means fully used, not "brand new." Named
# after the raw field (matching the AC/range_hood filterUsage convention,
# which counts the same direction) rather than "filter life," which would
# imply the opposite direction.
FILTER = Capability(
    href='/consumable/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='filter_progress', field='x.com.samsung.da.items',
                   unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda items: int_or_none(
                       _consumable_state(items, 'FilterProgress'))),
    ),
)

DEVICE_ACTIVE = Capability(
    href='/devicespecificinfo/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(key='device_active', field='x.com.samsung.da.deviceActive',
                          icon='mdi:check-network-outline',
                          entity_category='diagnostic',
                          value_fn=lambda v: bool(v)),
    ),
)

# OCF-native / vendor pair for fan speed+direction -- see module docstring for
# why these are read-only for now.
AIRFLOW_GENERIC = Capability(
    href='/airflow/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='fan_speed_level', field='speed',
                   icon='mdi:fan',
                   state_class='measurement', entity_category='diagnostic'),
        SensorDesc(key='fan_direction', field='direction',
                   icon='mdi:rotate-3d-variant',
                   entity_category='diagnostic'),
    ),
)

AIRFLOW_VS_FALLBACK = Capability(
    href='/airflow/vs/0',
    match_fn=lambda rep, resources: '/airflow/0' not in resources,
    poll_tier='warm',
    entities=(
        SensorDesc(key='fan_speed_level', field='x.com.samsung.da.speedLevel',
                   icon='mdi:fan',
                   state_class='measurement', entity_category='diagnostic',
                   value_fn=int_or_none),
        SensorDesc(key='fan_direction', field='x.com.samsung.da.direction',
                   icon='mdi:rotate-3d-variant',
                   entity_category='diagnostic'),
    ),
)


def _light_write(payload, rep, href=None):
    # option_write's single-token write is confirmed on a washer's
    # /course/vs/0 (issue #54), NOT independently on this family's
    # /mode/vs/0 -- extrapolated on the assumption the same vendor field
    # merges the same way everywhere. If some unit replaces the field
    # outright instead, this would drop Comode/OptionCode alongside it on
    # the next light toggle; revisit if a real device report surfaces that.
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write('Light', payload),
    }


def _pollution_write(payload, rep, href=None):
    # Same one-token option_write as _light_write, for the AVT board's
    # 'Pollution_*' flag. Confirmed on AVT-WW-TP1-23-AXX500 hardware:
    # toggling Pollution echoes back cleanly and leaves Light_* and the
    # opaque OptionCode_27514 token unchanged (so the single-token write
    # doesn't clobber the packed array on this board).
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': option_write('Pollution', payload),
    }


MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='display_light', icon='mdi:led-on',
                   entity_category='config',
                   rep_fn=bool_option_value('Light'),
                   exists_fn=bool_option_exists('Light'),
                   write_fn=_light_write),
        # AVT board's pollution/air-quality indicator flag (absent on the TVTL
        # family, so exists_fn gates it off there). Write confirmed on hardware.
        SwitchDesc(key='pollution_light', icon='mdi:weather-hazy',
                   entity_category='config', enabled_default=False,
                   rep_fn=bool_option_value('Pollution'),
                   exists_fn=bool_option_exists('Pollution'),
                   write_fn=_pollution_write),
        # Read-only -- confirmed NOT the fan-speed selector (see module
        # docstring), actual purpose still unconfirmed.
        SensorDesc(key='operating_mode', icon='mdi:fan',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Comode'),
                   exists_fn=bool_option_exists('Comode')),
    ),
)

# /humidity/0 and /humidity/vs/0 are empty {} on both dumps this family has
# been verified against -- covered here (not globally, per ignored.py's
# module docstring) since those hrefs collide with fridge/AC schemas
# elsewhere. Same two hrefs and reasoning as airconditioner.py's _AC_IGNORED.
COVERAGE = [
    Capability(href='/humidity/0'),
    Capability(href='/humidity/vs/0'),
]


# ===========================================================================
# AVT-WW-TP1 board sub-family (e.g. AVT-WW-TP1-23-AXX500). No oneUiVersion;
# routed here by for_device_by_model's 'AVT-' prefix rule. Shares the sensors/
# humidity/device-active/display-light caps above with the ARTIK051_TVTL family
# but exposes fan speed on /wind/strength and the filter on /filter/hepafilter
# (not /airflow + /consumable). Each cap below binds only when its href is
# present, so the two sub-families coexist in one registry. Write contracts for
# the fan, pollution light, and sensing interval were confirmed on real
# AVT-WW-TP1-23-AXX500 hardware.
# ===========================================================================
def _wind_fan_write(payload, rep, href=None):
    """fan.py's LocalThingsAirPurifierFan sends (kind, value[, target]):
      ('power', bool, target_href) -> power on/off on /power/0 or /power/vs/0;
      ('mode', code)               -> {"x.com.samsung.da.modes": code} SCALAR
                                      string to /wind/strength/vs/0.
    Scalar (not a list) and all five codes incl. Sleep 91 confirmed on hardware.
    """
    kind, value, *args = payload
    if kind == 'power':
        target = args[0] if args else '/power/0'
        if target == '/power/0':
            return ['power', '0'], {'value': bool(value)}
        if target == '/power/vs/0':
            return ['power', 'vs', '0'], {
                'x.com.samsung.da.power': 'On' if value else 'Off'}
        return None
    if kind == 'mode':
        code = str(value)
        if code not in [str(c) for c in rep.get('x.com.samsung.da.supportedModes', ())]:
            return None
        return ['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': code}
    return None


# All wind-strength codes are one flat set of fan modes (Auto/Low/Medium/High/
# Sleep) surfaced as the fan's preset_modes -- see fan.py. FanDesc key 'fan' is
# the device's primary entity (name comes from the device, catalog-exempt).
WIND_STRENGTH = Capability(
    href='/wind/strength/vs/0',
    poll_tier='warm',
    entities=(
        FanDesc(key='fan', field='x.com.samsung.da.modes', write_fn=_wind_fan_write),
    ),
)


def _filter_remaining_hours(rep):
    """Rated life minus hours used (dump: 8762 - 8 = 8754 h)."""
    cap = int_or_none(rep.get('x.com.samsung.da.filterCapacity'))
    used = int_or_none(rep.get('x.com.samsung.da.filterUsage'))
    if cap is None or used is None:
        return None
    return cap - used


def _filter_reset_write(payload, rep, href=None):
    # filterResetType advertises ['replaceable']; writing it back is the
    # hypothesised reset action (should zero filterUsage).
    #
    # Sent as a LIST, matching the shape the device reports. A scalar
    # 'replaceable' was tried first on AVT-WW-TP1-23-AXX500 hardware and the
    # device accepted the PUT without error but left filterUsage unchanged --
    # i.e. it silently ignored the wrong-shaped value. laundry.option_write
    # already establishes the convention for this codebase: a field that reads
    # as a list is written back as a list (x.com.samsung.da.options), so the
    # scalar was the anomaly.
    return ['filter', 'hepafilter', 'vs', '0'], {
        'x.com.samsung.da.filterResetType': [payload]}


# Reuses airconditioner._filter_usage_percent (used/capacity -> %) and the
# shared 'filter_status' enum catalog; adds the raw hour readings (matching
# range_hood.hood_filter_capacity's unit='h').
HEPA_FILTER = Capability(
    href='/filter/hepafilter/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='hepa_filter_usage', rep_fn=_filter_usage_percent,
                   unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic'),
        SensorDesc(key='hepa_filter_status', field='x.com.samsung.da.filterStatus',
                   device_class='enum', options=('normal', 'wash', 'replace'),
                   translation_key='filter_status',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda v: v.lower() if isinstance(v, str) else v),
        SensorDesc(key='hepa_filter_usage_hours', field='x.com.samsung.da.filterUsage',
                   unit='h', state_class='measurement',
                   icon='mdi:timer-sand', entity_category='diagnostic',
                   value_fn=int_or_none),
        SensorDesc(key='hepa_filter_life_remaining', rep_fn=_filter_remaining_hours,
                   unit='h', state_class='measurement',
                   icon='mdi:timer-outline', entity_category='diagnostic'),
        SensorDesc(key='hepa_filter_capacity', field='x.com.samsung.da.filterCapacity',
                   unit='h', icon='mdi:timer-outline', entity_category='diagnostic',
                   enabled_default=False, value_fn=int_or_none),
        ButtonDesc(key='filter_reset', payload='replaceable',
                   icon='mdi:air-filter', entity_category='config',
                   write_fn=_filter_reset_write),
    ),
)


def _interval_minutes(seconds):
    """Device stores the interval in seconds; the entity is in minutes."""
    secs = int_or_none(seconds)
    return round(secs / 60) if secs else None


def _interval_write(payload, rep, href=None):
    # Minutes in the UI -> seconds on the wire (scalar string). Arbitrary values
    # are honoured -- confirmed on hardware (60 s drove sensing every ~60 s).
    return ['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingInterval': str(int(round(float(payload) * 60)))}


def _sensing_mode(rep):
    """Fold the periodic-sensing toggle and the auto-action into one status:
    off (sensing disabled) / sensing_only (on, no auto-action) / auto_purify /
    st_alarm (SmartThings notification)."""
    on = str(rep.get('x.com.samsung.da.periodicSensingActivationState', '')).lower() == 'on'
    if not on:
        return 'off'
    return {'Airpurify': 'auto_purify', 'Alarm': 'st_alarm'}.get(
        str(rep.get('x.com.samsung.da.autoExeState', '')), 'sensing_only')


def _periodic_sensing_write(payload, rep, href=None):
    # The master on/off for the periodic air-quality response -- confirmed
    # writable on hardware. Off holds any auto_action pending; turning it On
    # arms the selected action. With it On + auto_action Off = "sense only".
    return ['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingActivationState': (
            'On' if payload == 'On' else 'Off')}


# One-control version of the sensing mode: a single /airlevelcheck PUT sets both
# the periodic-sensing toggle and the auto-action, so picking 'Only sensing'
# arms sensing with no action in one step (both fields live on this resource).
_SENSING_MODE_BODIES = {
    'off': {'x.com.samsung.da.periodicSensingActivationState': 'Off'},
    'sensing_only': {'x.com.samsung.da.periodicSensingActivationState': 'On',
                     'x.com.samsung.da.autoExeState': 'Off'},
    'auto_purify': {'x.com.samsung.da.periodicSensingActivationState': 'On',
                    'x.com.samsung.da.autoExeState': 'Airpurify'},
    'st_alarm': {'x.com.samsung.da.periodicSensingActivationState': 'On',
                 'x.com.samsung.da.autoExeState': 'Alarm'},
}


def _sensing_mode_write(payload, rep, href=None):
    body = _SENSING_MODE_BODIES.get(payload)
    return (['airlevelcheck', 'vs', '0'], dict(body)) if body else None


def _skip_status_write(payload, rep, href=None):
    return ['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingSkipStatus': (
            'On' if payload == 'On' else 'Off')}


# The "do not disturb" window during which periodic sensing is skipped, stored
# as one HHMMHHMM string (start+end) on periodicSensingSkipTime (e.g. "14002100"
# = 14:00-21:00). Split into two HA time entities; each write reads the other
# half back out of the live rep so the pair round-trips.
def _skip_time_read(part):
    def _read(value):
        raw = str(value or '')
        chunk = raw[0:4] if part == 'start' else raw[4:8]
        if len(chunk) == 4 and chunk.isdigit():
            try:
                return datetime.time(int(chunk[:2]), int(chunk[2:]))
            except ValueError:
                return None
        return None
    return _read


def _skip_time_write(part):
    def _write(value, rep, href=None):
        cur = (str(rep.get('x.com.samsung.da.periodicSensingSkipTime', '') or '')
               + '00000000')[:8]
        hhmm = f'{value.hour:02d}{value.minute:02d}'
        new = hhmm + cur[4:8] if part == 'start' else cur[0:4] + hhmm
        return ['airlevelcheck', 'vs', '0'], {
            'x.com.samsung.da.periodicSensingSkipTime': new}
    return _write


# /airlevelcheck/vs/0 -- periodic air-quality sensing engine. Read-only status
# reuses range_hood.AIR_LEVEL_CHECK's field/read patterns (and its catalog
# entries, via identical keys) plus the shared _timestamp helper. Confirmed
# writable on hardware: the sensing interval (free seconds) and the automatic
# action select (Off/Airpurify/Alarm, while periodic sensing is On). The
# 'sensing_mode' sensor folds the sensing toggle + auto-action into one status
# (incl. the "Only sensing" combination). Binding this href, it is removed from
# AVT_IGNORED below.
AIR_LEVEL_CHECK = Capability(
    href='/airlevelcheck/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='sensing_mode', rep_fn=_sensing_mode,
                   options=('off', 'sensing_only', 'auto_purify', 'st_alarm'),
                   translation_key='sensing_mode', icon='mdi:radar',
                   entity_category='config', write_fn=_sensing_mode_write),
        NumberDesc(key='sensing_interval',
                   field='x.com.samsung.da.periodicSensingInterval',
                   icon='mdi:timer-cog', entity_category='config',
                   native_min=1, native_max=60, step=1, unit='min',
                   value_fn=_interval_minutes, write_fn=_interval_write),
        SwitchDesc(key='periodic_air_sensing',
                   field='x.com.samsung.da.periodicSensingActivationState',
                   icon='mdi:radar', entity_category='config',
                   extra_attributes={
                       'description': 'Runs the selected AI Purify (Mode) '
                                      'while the air purifier is powered off'},
                   value_fn=lambda v: str(v).lower() == 'on',
                   write_fn=_periodic_sensing_write),
        SensorDesc(key='air_sensing_state', field='x.com.samsung.da.sensingState',
                   icon='mdi:radar', entity_category='diagnostic',
                   enabled_default=False),
        SensorDesc(key='last_air_sensing_time', field='x.com.samsung.da.lastSensingTime',
                   device_class='timestamp', entity_category='diagnostic',
                   value_fn=_timestamp),
        SensorDesc(key='last_air_sensing_level', field='x.com.samsung.da.lastSensingLevel',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   enabled_default=False),
        # Do-not-disturb: skip periodic sensing during a daily window
        # (periodicSensingSkipTime = HHMMHHMM, e.g. 14:00-21:00).
        SwitchDesc(key='periodic_sensing_skip_status',
                   field='x.com.samsung.da.periodicSensingSkipStatus',
                   icon='mdi:sleep', entity_category='config',
                   value_fn=lambda v: str(v).lower() == 'on',
                   write_fn=_skip_status_write),
        TimeDesc(key='sensing_skip_start',
                 field='x.com.samsung.da.periodicSensingSkipTime',
                 icon='mdi:clock-start', entity_category='config',
                 value_fn=_skip_time_read('start'), write_fn=_skip_time_write('start')),
        TimeDesc(key='sensing_skip_end',
                 field='x.com.samsung.da.periodicSensingSkipTime',
                 icon='mdi:clock-end', entity_category='config',
                 value_fn=_skip_time_read('end'), write_fn=_skip_time_write('end')),
    ),
)

# Opaque/plumbing hrefs this board exposes -- ignored (no-entity caps) so
# discover() reports no gap. (/airlevelcheck is NOT here: bound above.)
AVT_IGNORED = [
    '/availablecontrolsets/vs/0',  # opaque hex-encoded control-set bitmap
    '/da/softreset/vs/0',          # soft-reset trigger plumbing
    '/mode/convenient/vs/0',       # supportedModes is ['Off'] only -- no choice
    '/dnd/autosleep/vs/0',         # DND schedule, visible='false' -- not exposed
]

AVT_COVERAGE = [Capability(href=h) for h in AVT_IGNORED]

# The connected Wi-Fi network name. /wirelessinfo/vs/0 is in the global
# ignored.IGNORED (MACs are sensitive), so the air-purifier registry drops that
# ignore for this href and binds this instead (SSID only, MACs left unmodeled).
WIRELESS = Capability(
    href='/wirelessinfo/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='wifi_ssid', field='connectedApSsid',
                   icon='mdi:wifi', entity_category='diagnostic'),
    ),
)


def _no_fan_board(rep, resources):
    """The standalone power switch is redundant on boards that expose a fan
    entity (/wind/strength) -- the fan owns on/off there -- so suppress it. Keep
    it for the ARTIK051_TVTL family, which has no fan and no /wind/strength."""
    return '/wind/strength/vs/0' not in resources


# common.POWER's switch, gated on _no_fan_board so it is created only for the
# fan-less TVTL boards; on AVT boards the LocalThingsAirPurifierFan on/off is
# the power control. Replaces common.POWER in the by_type registry. /power/0 and
# /power/vs/0 stay covered (a declining match_fn is not a coverage gap), and the
# fan still reads/writes them directly off the coordinator snapshot.
POWER_SWITCH = Capability(
    href='/power/0', match_fn=_no_fan_board,
    entities=(
        SwitchDesc(key='power_switch', field='value',
                   value_fn=lambda v: bool(v),
                   write_fn=lambda p, rep, href=None: (
                       ['power', '0'], {'value': p == 'On'})),
    ),
)

POWER_SWITCH_VS = Capability(
    href='/power/vs/0',
    match_fn=lambda rep, resources: (
        '/power/0' not in resources and _no_fan_board(rep, resources)),
    entities=(
        SwitchDesc(key='power_switch', field='x.com.samsung.da.power',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['power', 'vs', '0'],
                       {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

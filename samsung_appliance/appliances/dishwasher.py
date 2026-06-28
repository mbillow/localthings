"""Dishwasher descriptor (Samsung DW9000F-class / ADW-WW-RTL-24-AILITE).

Resource map captured 2026-06-27 via DTLS-CoAP with the ab0b0ac4 cert
from a DW90F89T0USRAA at 10.0.0.129:49154.
See local-tools/dumps/10.0.0.129.json for the full field reference.

Write surfaces exposed by this descriptor:

  modelled on dryer/operational-state write semantics (same firmware family):
    * Start / Pause / Stop via /operational/state/vs/0
      — requires Remote Control on.

  modelled on oven option-write semantics (unproven, first HA use is the test):
    * Sound mode select via /settings/sound/mode/vs/0
    * Door LED brightness select via /doorled/light/vs/0
    * Door LED night light switch via /doorled/light/vs/0

Course selection is not yet exposed — the course encoding in
/wm/editcourse/vs/0 uses an opaque hex string (EditCourseList_…) whose
byte-to-course-name mapping requires the same capture process as the
dryer's COURSE_NAMES table. Capture with the dishwasher running each
course and compare /course/vs/0 + /dishwasher/vs/0 before/after.
"""
import time

from .base import (
    ApplianceDescriptor,
    avail_base,
    avail_with_remote,
    device_block,
    encode,
)
from ..poll_scheduler import PollTier


# --- OBSERVE paths ---------------------------------------------------------
# Only /<x>/vs/0 siblings push notifications. /course/vs/0 and
# /dishwasher/vs/0 were included in the device tree but returned no
# parseable state fields in the 2026-06-27 capture — include them so
# future firmware pushes surface through the cache.
OBSERVE_PATHS = [
    ['operational', 'state', 'vs', '0'],        # state, progress, remainingTime
    ['power',       'vs', '0'],                 # power On/Off
    ['kidslock',    'vs', '0'],                 # child lock
    ['remotectrl',  'vs', '0'],                 # remote control enabled
    ['energy',      'consumption', 'vs', '0'],  # watts + cumulative Wh
    ['water',       'consumption', 'vs', '0'],  # cumulative water (mL)
    ['alarms',      'vs', '0'],
    ['course',      'vs', '0'],
    ['dishwasher',  'vs', '0'],
    ['doorled',     'light', 'vs', '0'],        # door LED brightness + night light
    ['settings',    'sound', 'mode', 'vs', '0'],
    ['wm',          'jobbeginingstatus', 'vs', '0'],
]


_SAMSUNG_STATE_TO_OCF = {
    'Ready':   'idle',
    'Run':     'active',
    'Running': 'active',
    'Pause':   'pause',
    'Paused':  'pause',
    'End':     'idle',
    'Stop':    'idle',
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --- flatten ---------------------------------------------------------------
def flatten(links):
    g = lambda href, k, default=None: (links.get(href) or {}).get(k, default)

    sam_state = g('/operational/state/vs/0', 'x.com.samsung.da.state')
    machine_state = (_SAMSUNG_STATE_TO_OCF.get(sam_state, sam_state)
                     if sam_state is not None else None)

    progress = g('/operational/state/vs/0', 'x.com.samsung.da.progress')
    if progress in (None, 'None'):
        progress = 'Idle'

    remaining = g('/operational/state/vs/0', 'x.com.samsung.da.remainingTime')
    rem_min = None
    if remaining:
        try:
            h, m, s = remaining.split(':')
            rem_min = int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
        except Exception:
            pass

    delay_start = g('/operational/state/vs/0', 'x.com.samsung.da.delayStartTime')

    # Energy — same phantom -500W idle issue as the dryer.
    inst_w = _num(g('/energy/consumption/vs/0', 'x.com.samsung.da.instantaneousPower'))
    cum_wh = _num(g('/energy/consumption/vs/0', 'x.com.samsung.da.cumulativePower'))
    if inst_w is not None and inst_w < 0:
        inst_w = 0.0

    # Water — cumulative in mL, exposed as liters.
    cum_water_ml = _num(g('/water/consumption/vs/0', 'x.com.samsung.da.cumulativeWater'))
    cum_water_l = round(cum_water_ml / 1000.0, 1) if cum_water_ml is not None else None

    sam_power = g('/power/vs/0', 'x.com.samsung.da.power')
    sam_kids  = g('/kidslock/vs/0', 'x.com.samsung.da.kidsLock')
    sam_rc    = g('/remotectrl/vs/0', 'x.com.samsung.da.remoteControlEnabled')
    power_bin = (sam_power == 'On') if sam_power is not None else None
    kids_bin  = (sam_kids != 'Ready') if sam_kids is not None else None
    rc_bin    = (str(sam_rc).lower() == 'true') if sam_rc is not None else None

    filter_usage  = _int(g('/filter/waterfilter/vs/0', 'x.com.samsung.da.filterUsage'))
    filter_status = g('/filter/waterfilter/vs/0', 'x.com.samsung.da.filterStatus')

    alarm_items = g('/alarms/vs/0', 'x.com.samsung.da.items') or []
    alarm_code  = (alarm_items[-1].get('x.com.samsung.da.code')
                   if alarm_items else None)

    # Door LED — keys have no x.com.samsung.da. prefix on this appliance.
    led_rep = links.get('/doorled/light/vs/0') or {}
    led_brightness  = led_rep.get('setBrightness')
    led_night_light = led_rep.get('setNightLight')

    sound_rep  = links.get('/settings/sound/mode/vs/0') or {}
    sound_mode = sound_rep.get('mode')

    return {
        'machine_state':        machine_state,
        'progress':             progress,
        'progress_percentage':  _int(g('/operational/state/vs/0',
                                        'x.com.samsung.da.progressPercentage')),
        'completion_time':      remaining,
        'completion_minutes':   rem_min,
        'delay_start_time':     delay_start,
        'power_state':          sam_power,
        'power_state_binary':   power_bin,
        'child_lock':           sam_kids,
        'child_lock_binary':    kids_bin,
        'remote_control':       sam_rc,
        'remote_control_binary': rc_bin,
        'power_watts':          inst_w,
        'energy_kwh':           round(cum_wh / 1000.0, 2)
                                  if cum_wh is not None else None,
        'water_liters':         cum_water_l,
        'filter_usage':         filter_usage,
        'filter_status':        filter_status,
        'alarm_code':           alarm_code,
        'led_brightness':       led_brightness,
        'led_night_light':      led_night_light,
        'sound_mode':           sound_mode,
    }


# --- Remaining-time anchor + extrapolation --------------------------------
def on_observation(state, href, rep):
    if href != '/operational/state/vs/0':
        return
    rem = rep.get('x.com.samsung.da.remainingTime')
    if not isinstance(rem, str):
        return
    try:
        h, m, s = rem.split(':')
        state['remaining_anchor'] = (time.time(),
                                     int(h) * 3600 + int(m) * 60 + int(s))
    except (ValueError, AttributeError):
        pass


def project(state, sensors):
    anchor = state.get('remaining_anchor')
    if sensors.get('machine_state') != 'active' or anchor is None:
        return sensors
    ts, total = anchor
    remaining = max(0, int(total - (time.time() - ts)))
    h, rest = divmod(remaining, 3600)
    m, s = divmod(rest, 60)
    sensors = dict(sensors)
    sensors['completion_time'] = f"{h}:{m:02d}:{s:02d}"
    sensors['completion_minutes'] = h * 60 + m + (1 if s > 0 else 0)
    return sensors


def log_state_change(sensors):
    return (f"machine={sensors.get('machine_state')} "
            f"progress={sensors.get('progress')} "
            f"power={sensors.get('power_watts')}W")


# --- HA discovery ---------------------------------------------------------
MODEL = 'OCF dishwasher (TizenRT-iotivity, DW9000F-class)'

_SENSORS = [
    ('machine_state',       'Machine state',      {'icon': 'mdi:dishwasher'}),
    ('progress',            'Progress',           {'icon': 'mdi:progress-wrench'}),
    ('progress_percentage', 'Progress percent',
        {'unit_of_measurement': '%', 'state_class': 'measurement'}),
    ('completion_time',     'Completion time',    {'icon': 'mdi:timer-sand'}),
    ('completion_minutes',  'Remaining minutes',
        {'unit_of_measurement': 'min', 'device_class': 'duration',
         'state_class': 'measurement'}),
    ('delay_start_time',    'Delay start time',   {'icon': 'mdi:timer-pause'}),
    ('power_state',         'Power state',        {}),
    ('power_watts',         'Power',
        {'unit_of_measurement': 'W', 'device_class': 'power',
         'state_class': 'measurement'}),
    ('energy_kwh',          'Energy',
        {'unit_of_measurement': 'kWh', 'device_class': 'energy',
         'state_class': 'total_increasing'}),
    ('water_liters',        'Water consumption',
        {'unit_of_measurement': 'L', 'device_class': 'water',
         'state_class': 'total_increasing', 'icon': 'mdi:water'}),
    ('filter_usage',        'Filter usage',
        {'unit_of_measurement': '%', 'state_class': 'measurement',
         'icon': 'mdi:filter'}),
    ('filter_status',       'Filter status',      {'icon': 'mdi:filter-check'}),
    ('alarm_code',          'Alarm code',
        {'icon': 'mdi:alert', 'entity_category': 'diagnostic'}),
    ('sound_mode',          'Sound mode',         {'icon': 'mdi:volume-high'}),
]

_BINARY_SENSORS = [
    ('running', 'Running',
        "{{ 'ON' if value_json.machine_state == 'active' else 'OFF' }}",
        'running'),
    ('child_lock_active', 'Child lock',
        "{{ 'ON' if value_json.child_lock_binary else 'OFF' }}",
        'lock'),
    ('remote_control_enabled', 'Remote control',
        "{{ 'ON' if value_json.remote_control_binary else 'OFF' }}",
        'connectivity'),
]

CMD_OPERATIONAL  = 'cmd/operational_state'
CMD_SOUND_MODE   = 'cmd/sound_mode'
CMD_LED_BRIGHT   = 'cmd/led_brightness'
CMD_LED_NIGHT    = 'cmd/led_night_light'

_SOUND_MODES = ['voice', 'tone', 'mute']
_LED_LEVELS  = ['Low', 'High']


def build_discovery(topic_prefix, ha_prefix, device_name):
    state_topic  = f"{topic_prefix}/state"
    avail_topic  = f"{topic_prefix}/availability"
    remote_topic = f"{topic_prefix}/remote_available"
    dev = device_block(topic_prefix, device_name, MODEL)
    out = []

    for key, name, extra in _SENSORS:
        cfg = {
            'name':           name,
            'unique_id':      f"{topic_prefix}_{key}",
            'object_id':      f"{topic_prefix}_{key}",
            'state_topic':    state_topic,
            'value_template': f"{{{{ value_json.{key} }}}}",
            'availability':   avail_base(avail_topic),
            'device':         dev,
        }
        cfg.update(extra)
        out.append((f"{ha_prefix}/sensor/{topic_prefix}/{key}/config",
                    encode(cfg)))

    for key, name, template, dclass in _BINARY_SENSORS:
        cfg = {
            'name':           name,
            'unique_id':      f"{topic_prefix}_{key}",
            'object_id':      f"{topic_prefix}_{key}",
            'state_topic':    state_topic,
            'value_template': template,
            'payload_on':     'ON',
            'payload_off':    'OFF',
            'device_class':   dclass,
            'availability':   avail_base(avail_topic),
            'device':         dev,
        }
        out.append((f"{ha_prefix}/binary_sensor/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # buttons: Start / Pause / Stop (RC-gated)
    for key, name, payload, icon in [
        ('start', 'Start cycle',  'Run',   'mdi:play'),
        ('pause', 'Pause cycle',  'Pause', 'mdi:pause'),
        ('stop',  'Stop cycle',   'Ready', 'mdi:stop'),
    ]:
        cfg = {
            'name':              name,
            'unique_id':         f"{topic_prefix}_{key}",
            'object_id':         f"{topic_prefix}_{key}",
            'command_topic':     f"{topic_prefix}/{CMD_OPERATIONAL}",
            'payload_press':     payload,
            'icon':              icon,
            'availability':      avail_with_remote(avail_topic, remote_topic),
            'availability_mode': 'all',
            'device':            dev,
        }
        out.append((f"{ha_prefix}/button/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # select: sound mode
    cfg = {
        'name':          'Sound mode',
        'unique_id':     f"{topic_prefix}_sound_mode_select",
        'object_id':     f"{topic_prefix}_sound_mode_select",
        'state_topic':   state_topic,
        'value_template': '{{ value_json.sound_mode }}',
        'command_topic': f"{topic_prefix}/{CMD_SOUND_MODE}",
        'options':       _SOUND_MODES,
        'icon':          'mdi:volume-high',
        'availability':  avail_base(avail_topic),
        'device':        dev,
    }
    out.append((f"{ha_prefix}/select/{topic_prefix}/sound_mode/config",
                encode(cfg)))

    # select: door LED brightness
    cfg = {
        'name':          'Door LED brightness',
        'unique_id':     f"{topic_prefix}_led_brightness_select",
        'object_id':     f"{topic_prefix}_led_brightness_select",
        'state_topic':   state_topic,
        'value_template': '{{ value_json.led_brightness }}',
        'command_topic': f"{topic_prefix}/{CMD_LED_BRIGHT}",
        'options':       _LED_LEVELS,
        'icon':          'mdi:brightness-6',
        'availability':  avail_base(avail_topic),
        'device':        dev,
    }
    out.append((f"{ha_prefix}/select/{topic_prefix}/led_brightness/config",
                encode(cfg)))

    # switch: door LED night light
    cfg = {
        'name':           'Door LED night light',
        'unique_id':      f"{topic_prefix}_led_night_light_switch",
        'object_id':      f"{topic_prefix}_led_night_light_switch",
        'state_topic':    state_topic,
        'value_template': '{{ value_json.led_night_light }}',
        'state_on':       'On',
        'state_off':      'Off',
        'command_topic':  f"{topic_prefix}/{CMD_LED_NIGHT}",
        'payload_on':     'On',
        'payload_off':    'Off',
        'icon':           'mdi:weather-night',
        'availability':   avail_base(avail_topic),
        'device':         dev,
    }
    out.append((f"{ha_prefix}/switch/{topic_prefix}/led_night_light/config",
                encode(cfg)))

    return out


# --- MQTT command handlers ------------------------------------------------
def command_handlers():
    def _operational(p, _links):
        if p not in ('Run', 'Pause', 'Ready'):
            return None
        return ['operational', 'state', 'vs', '0'], {
            'x.com.samsung.da.state': p,
        }

    def _sound_mode(p, _links):
        if p not in _SOUND_MODES:
            return None
        return ['settings', 'sound', 'mode', 'vs', '0'], {'mode': p}

    def _led_brightness(p, _links):
        if p not in _LED_LEVELS:
            return None
        return ['doorled', 'light', 'vs', '0'], {'setBrightness': p}

    def _led_night(p, _links):
        if p not in ('On', 'Off'):
            return None
        return ['doorled', 'light', 'vs', '0'], {'setNightLight': p}

    return {
        CMD_OPERATIONAL: _operational,
        CMD_SOUND_MODE:  _sound_mode,
        CMD_LED_BRIGHT:  _led_brightness,
        CMD_LED_NIGHT:   _led_night,
    }


# --- Poll tiers -----------------------------------------------------------
# Same washer-family firmware as the dryer (~14 req/s empirical ceiling).
DISHWASHER_POLL_TIERS = [
    PollTier(
        name='hot',
        interval_s=1.0,
        active_interval_s=0.5,
        paths=(
            ('operational', 'state', 'vs', '0'),
        ),
    ),
    PollTier(
        name='warm',
        interval_s=15.0,
        paths=(
            ('power', 'vs', '0'),
            ('kidslock', 'vs', '0'),
            ('remotectrl', 'vs', '0'),
            ('alarms', 'vs', '0'),
            ('energy', 'consumption', 'vs', '0'),
            ('water', 'consumption', 'vs', '0'),
            ('filter', 'waterfilter', 'vs', '0'),
            ('doorled', 'light', 'vs', '0'),
            ('settings', 'sound', 'mode', 'vs', '0'),
            ('wm', 'jobbeginingstatus', 'vs', '0'),
        ),
    ),
    PollTier(
        name='sweep',
        interval_s=300.0,
        paths=(('device', '0'),),
        is_sweep=True,
    ),
]


def _is_active(links: dict) -> bool:
    rep = links.get('/operational/state/vs/0') or {}
    sam_state = rep.get('x.com.samsung.da.state')
    return _SAMSUNG_STATE_TO_OCF.get(sam_state) == 'active'


# --- Descriptor -----------------------------------------------------------
DISHWASHER = ApplianceDescriptor(
    name='dishwasher',
    default_observe_port=49154,
    observe_paths=OBSERVE_PATHS,
    seed_path=['device', '0'],
    flatten=flatten,
    build_discovery=build_discovery,
    command_handlers=command_handlers,
    on_observation=on_observation,
    project=project,
    remote_available_field='remote_control_binary',
    log_state_change=log_state_change,
    poll_tiers=DISHWASHER_POLL_TIERS,
    is_active=_is_active,
)

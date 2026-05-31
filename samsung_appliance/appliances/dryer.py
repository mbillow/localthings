"""Dryer descriptor.

Lifts the dryer-specific OBSERVE paths, sensor flattening, HA discovery
inventory, and MQTT command handlers out of the original
samsung_dryer/{bridge,sensors,discovery}.py modules into one place.
"""
import time

from .base import (
    ApplianceDescriptor,
    avail_base,
    avail_with_remote,
    device_block,
    encode,
)


# --- OBSERVE paths -----------------------------------------------------
# Only Samsung's `/<x>/vs/0` siblings actually push notifications; the
# OCF-standard `/<x>/0` paths accept registration silently but never
# fire. flatten() derives the OCF-shaped values from the live /vs/0
# strings.
OBSERVE_PATHS = [
    ['operational', 'state', 'vs', '0'],     # state, remainingTime, progress
    ['power',       'vs', '0'],              # power on/off
    ['kidslock',    'vs', '0'],              # child lock
    ['remotectrl',  'vs', '0'],              # remote control enabled
    ['energy',      'consumption', 'vs', '0'],
    ['course',      'vs', '0'],
    ['washer',      'vs', '0'],              # dryLevel, dryTime, type
    ['diagnosis',   'vs', '0'],
    ['alarms',      'vs', '0'],
    ['st',          'dryercourse', 'vs', '0'],
    ['wm',          'jobbeginingstatus', 'vs', '0'],
]


# --- Course table ------------------------------------------------------
# Captured 2026-05-29 by dialing every course on a
# DA_WM_TP2_20_COMMON_DV5000T dryer. Other Samsung dryers may report a
# different Table_NN; capture a fresh table for them with
# local-tools/course_mapper.py.
COURSE_NAMES = {
    'Table_03': {
        0x16: 'Cotton',
        0x18: 'Synthetics',
        0x19: 'Delicates',
        0x1A: 'Wool',
        0x1B: 'Bedding',
        0x1C: 'Shirts',
        0x1D: 'Towels',
        0x1E: 'Outdoor',
        0x1F: 'Mixed Load',
        0x20: 'Iron Dry',
        0x23: 'Quick Dry 35',
        0x24: 'Cool Air',
        0x25: 'Warm Air',
        0x27: 'Time Dry',
    },
}

_COURSE_CODE_BY_NAME = {
    name: code
    for table_codes in COURSE_NAMES.values()
    for code, name in table_codes.items()
}


def _decode_course(s):
    """`Table_03_Course_16` → `Cotton`. Pass through verbatim if the
    table or code isn't in our lookup."""
    if not isinstance(s, str) or '_Course_' not in s:
        return s
    table_part, _, code_str = s.partition('_Course_')
    table = COURSE_NAMES.get(table_part)
    if not table:
        return s
    try:
        code = int(code_str, 16)
    except ValueError:
        return s
    return table.get(code, s)


def _encode_course(name):
    """`Cotton` → `Course_16`. Returns None for unknown names so the
    caller refuses rather than POST garbage."""
    code = _COURSE_CODE_BY_NAME.get(name)
    if code is None:
        return None
    return f"Course_{code:02X}"


def _course_options():
    """Stable-sorted human course names for the HA select dropdown."""
    return sorted(_COURSE_CODE_BY_NAME.keys())


# --- Samsung-state → OCF currentMachineState ---------------------------
_SAMSUNG_STATE_TO_OCF = {
    'Ready':   'idle',
    'Run':     'active',
    'Running': 'active',
    'Pause':   'pause',
    'Paused':  'pause',
    'End':     'idle',
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


# --- flatten -----------------------------------------------------------
def flatten(links):
    """Map a /device/0 link dict to the flat sensor dict that's
    published to MQTT. Every field reads from `/<x>/vs/0` paths so push
    updates immediately drive every entity."""
    g = lambda href, k, default=None: (links.get(href) or {}).get(k, default)

    inst_w = _num(g('/energy/consumption/vs/0',
                    'x.com.samsung.da.instantaneousPower'))
    cum_wh = _num(g('/energy/consumption/vs/0',
                    'x.com.samsung.da.cumulativePower'))
    if inst_w is not None and inst_w < 0:
        # The dryer reports a phantom -500W when idle; HA energy
        # dashboard hates negatives.
        inst_w = 0.0

    sam_state = g('/operational/state/vs/0', 'x.com.samsung.da.state')
    machine_state = (_SAMSUNG_STATE_TO_OCF.get(sam_state, sam_state)
                     if sam_state is not None
                     else g('/operational/state/0', 'currentMachineState'))

    progress = g('/operational/state/vs/0', 'x.com.samsung.da.progress')
    job_state = progress or g('/operational/state/0', 'currentJobState')
    # HA's value_template treats the literal "None" as null (renders as
    # "Unknown"). Substitute something we can render verbatim.
    if job_state in (None, 'None'):
        job_state = 'Idle'
    if progress in (None, 'None'):
        progress = 'Idle'

    remaining = (g('/operational/state/vs/0',
                   'x.com.samsung.da.remainingTime')
                 or g('/operational/state/0', 'remainingTime'))
    rem_min = None
    if remaining:
        try:
            h, m, s = remaining.split(':')
            rem_min = int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
        except Exception:
            pass

    sam_power = g('/power/vs/0', 'x.com.samsung.da.power')
    sam_kids  = g('/kidslock/vs/0', 'x.com.samsung.da.kidsLock')
    sam_rc    = g('/remotectrl/vs/0',
                  'x.com.samsung.da.remoteControlEnabled')
    power_bin = (sam_power == 'On') if sam_power is not None else None
    kids_bin  = (sam_kids != 'Ready') if sam_kids is not None else None
    rc_bin    = (str(sam_rc).lower() == 'true') if sam_rc is not None else None

    return {
        'machine_state':         machine_state,
        'job_state':             job_state,
        'progress':              progress,
        'progress_percentage':   _int(g('/operational/state/vs/0',
                                        'x.com.samsung.da.progressPercentage')
                                       or g('/operational/state/0',
                                            'progressPercentage')),
        'completion_time':       remaining,
        'completion_minutes':    rem_min,
        'delay_end_time':        g('/operational/state/vs/0',
                                   'x.com.samsung.da.delayEndTime'),
        'power_state':           sam_power,
        'power_state_binary':    power_bin,
        'child_lock':            sam_kids,
        'child_lock_binary':     kids_bin,
        'remote_control':        sam_rc,
        'remote_control_binary': rc_bin,
        'power_watts':           inst_w,
        'energy_kwh':            round(cum_wh / 1000.0, 2)
                                    if cum_wh is not None else None,
        'energy_wh_cumulative':  int(cum_wh) if cum_wh is not None else None,
        'dryer_mode':            _decode_course(
                                     g('/st/dryercourse/vs/0',
                                       'x.com.samsung.da.st.dryerMode')),
        'dry_level':             _int(g('/washer/vs/0',
                                        'x.com.samsung.da.dryLevel')),
        'dry_time':              g('/washer/vs/0',
                                   'x.com.samsung.da.dryTime'),
        'dryer_type':            g('/washer/vs/0',
                                   'x.com.samsung.da.dryerType'),
        'wrinkle_prevent':       g('/washer/vs/0',
                                   'x.com.samsung.da.wrinklePrevent'),
        'diagnosis':             g('/diagnosis/vs/0',
                                   'x.com.samsung.da.diagnosisStart'),
        'country_code':          g('/configuration/vs/0',
                                   'x.com.samsung.da.countryCode'),
    }


# --- Remaining-time anchor + extrapolation ----------------------------
# The dryer pushes /operational/state/vs/0 on state transitions but not
# on remainingTime ticks. Anchor = (timestamp, total_seconds) at last
# push; project() extrapolates downward while machine_state == 'active'.

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


# --- Log-line ----------------------------------------------------------
def log_state_change(sensors):
    return (f"machine={sensors.get('machine_state')} "
            f"power={sensors.get('power_watts')}W "
            f"energy={sensors.get('energy_kwh')}kWh")


# --- HA discovery ------------------------------------------------------
MODEL = 'OCF dryer (TizenRT-iotivity)'

# (key, friendly name, extra-config-dict)
_SENSORS = [
    ('machine_state',       'Machine state',       {'icon': 'mdi:tumble-dryer'}),
    ('job_state',           'Job state',           {}),
    ('progress',            'Progress',            {}),
    ('progress_percentage', 'Progress percent',
        {'unit_of_measurement': '%', 'state_class': 'measurement'}),
    ('completion_time',     'Completion time',     {'icon': 'mdi:timer-sand'}),
    ('completion_minutes',  'Remaining minutes',
        {'unit_of_measurement': 'min', 'device_class': 'duration',
         'state_class': 'measurement'}),
    ('delay_end_time',      'Delay end time',      {'icon': 'mdi:timer'}),
    ('power_state',         'Power state',         {}),
    ('power_watts',         'Power',
        {'unit_of_measurement': 'W', 'device_class': 'power',
         'state_class': 'measurement'}),
    ('energy_kwh',          'Energy',
        {'unit_of_measurement': 'kWh', 'device_class': 'energy',
         'state_class': 'total_increasing'}),
    ('dryer_mode',          'Dryer mode',          {}),
    ('dry_level',           'Dry level',           {}),
    ('dry_time',            'Dry time',            {}),
    ('dryer_type',          'Dryer type',          {}),
    ('wrinkle_prevent',     'Wrinkle prevent',     {}),
    ('diagnosis',           'Diagnosis',           {}),
    ('country_code',        'Country code',        {}),
]

# (key, friendly name, value_template, device_class)
_BINARY_SENSORS = [
    ('running', 'Running',
        "{{ 'ON' if value_json.machine_state == 'active' else 'OFF' }}",
        'running'),
    ('power_switch', 'Power switch',
        "{{ 'ON' if value_json.power_state_binary else 'OFF' }}",
        'power'),
    ('child_lock_active', 'Child lock',
        "{{ 'ON' if value_json.child_lock_binary else 'OFF' }}",
        'lock'),
    ('remote_control_enabled', 'Remote control',
        "{{ 'ON' if value_json.remote_control_binary else 'OFF' }}",
        'connectivity'),
]

# MQTT command-topic suffixes. The bridge subscribes to <prefix>/cmd/#
# and dispatches by suffix.
CMD_WRINKLE_PREVENT = 'cmd/wrinkle_prevent'
CMD_OPERATIONAL     = 'cmd/operational_state'
CMD_DRYER_MODE      = 'cmd/dryer_mode'


def build_discovery(topic_prefix, ha_prefix, device_name):
    """Return list of (discovery_topic, payload_bytes) tuples ready to
    publish (retained) on MQTT connect."""
    state_topic   = f"{topic_prefix}/state"
    avail_topic   = f"{topic_prefix}/availability"
    remote_topic  = f"{topic_prefix}/remote_available"
    dev = device_block(topic_prefix, device_name, MODEL)
    out = []

    # read-only sensors
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

    # switch: wrinkle prevent (always available)
    cfg = {
        'name':           'Wrinkle prevent',
        'unique_id':      f"{topic_prefix}_wrinkle_prevent_switch",
        'object_id':      f"{topic_prefix}_wrinkle_prevent_switch",
        'state_topic':    state_topic,
        'value_template': '{{ value_json.wrinkle_prevent }}',
        'state_on':       'On',
        'state_off':      'Off',
        'command_topic':  f"{topic_prefix}/{CMD_WRINKLE_PREVENT}",
        'payload_on':     'On',
        'payload_off':    'Off',
        'icon':           'mdi:iron',
        'availability':   avail_base(avail_topic),
        'device':         dev,
    }
    out.append((f"{ha_prefix}/switch/{topic_prefix}/wrinkle_prevent/config",
                encode(cfg)))

    # buttons: Start / Pause / Stop (gated on remote control)
    buttons = [
        ('start', 'Start cycle', 'Run',   'mdi:play'),
        ('pause', 'Pause cycle', 'Pause', 'mdi:pause'),
        ('stop',  'Stop cycle',  'Ready', 'mdi:stop'),
    ]
    for key, name, payload_press, icon in buttons:
        cfg = {
            'name':              name,
            'unique_id':         f"{topic_prefix}_{key}",
            'object_id':         f"{topic_prefix}_{key}",
            'command_topic':     f"{topic_prefix}/{CMD_OPERATIONAL}",
            'payload_press':     payload_press,
            'icon':               icon,
            'availability':      avail_with_remote(avail_topic, remote_topic),
            'availability_mode': 'all',
            'device':            dev,
        }
        out.append((f"{ha_prefix}/button/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # select: course (gated on remote control)
    cfg = {
        'name':              'Course',
        'unique_id':         f"{topic_prefix}_course_select",
        'object_id':         f"{topic_prefix}_course_select",
        'state_topic':       state_topic,
        'value_template':    '{{ value_json.dryer_mode }}',
        'command_topic':     f"{topic_prefix}/{CMD_DRYER_MODE}",
        'options':           _course_options(),
        'icon':              'mdi:tumble-dryer',
        'availability':      avail_with_remote(avail_topic, remote_topic),
        'availability_mode': 'all',
        'device':            dev,
    }
    out.append((f"{ha_prefix}/select/{topic_prefix}/course/config",
                encode(cfg)))

    return out


# --- MQTT command handlers --------------------------------------------
def command_handlers():
    """topic_suffix → fn(payload, links) → (path_segs, body_dict) | None.

    `None` means refuse the command (caller logs & drops). Dryer
    handlers don't need the links snapshot — they're all single-field
    writes."""
    def _wrinkle(p, _links):
        if p not in ('On', 'Off'):
            return None
        return ['washer', 'vs', '0'], {'x.com.samsung.da.wrinklePrevent': p}

    def _operational(p, _links):
        if p not in ('Run', 'Pause', 'Ready'):
            return None
        return ['operational', 'state', 'vs', '0'], {'x.com.samsung.da.state': p}

    def _course(p, _links):
        code = _encode_course(p)
        if code is None:
            return None
        return ['st', 'dryercourse', 'vs', '0'], {'x.com.samsung.da.st.dryerMode': code}

    return {
        CMD_WRINKLE_PREVENT: _wrinkle,
        CMD_OPERATIONAL:     _operational,
        CMD_DRYER_MODE:      _course,
    }


# --- Descriptor --------------------------------------------------------
DRYER = ApplianceDescriptor(
    name='dryer',
    default_observe_port=49155,
    observe_paths=OBSERVE_PATHS,
    seed_path=['device', '0'],
    flatten=flatten,
    build_discovery=build_discovery,
    command_handlers=command_handlers,
    on_observation=on_observation,
    project=project,
    remote_available_field='remote_control_binary',
    log_state_change=log_state_change,
)

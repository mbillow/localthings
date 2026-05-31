"""Oven descriptor (Samsung NV7000BS-class).

Resource map captured 2026-05-31 via DTLS-CoAP with the ab0b0ac4 cert.
See `local-tools/comparisons/oven-tree.md` for the full field reference.

Write surfaces this descriptor exposes:

  proven:
    * UpperLamp via /mode/vs/0 options RMW (probe_oven_lamp_toggle.py)
      — works even with Remote Control off.

  unproven (first HA use is also the test):
    * Sound, FastPreheat — same RMW pattern as lamp.
    * Setpoint via /temperatures/vs/0 items RMW. Mid-cook write may
      or may not retune the element (plan §K-U #2).
    * Mode select via /mode/vs/0 .modes — mid-cook acceptance unknown
      (plan §K-U #3).
    * Power on/off via /power/vs/0.
    * Stop via /operational/state/vs/0 (dryer convention; oven may
      use a different state value).

Untested writes are gated behind <prefix>/remote_available so HA
disables them in the UI when the oven's Remote Control switch is off."""
import time

from .base import (
    ApplianceDescriptor,
    avail_base,
    avail_with_cycle,
    avail_with_remote_and_cycle,
    device_block,
    encode,
)


# ---------------------------------------------------------------------
# OBSERVE paths — every push-eligible /<x>/vs/0 resource on the oven.
# Same wedge-safety story as the dryer: only `/<x>/vs/0` siblings push;
# OCF-standard `/<x>/0` paths accept registration but never fire.
# Security paths (/oic/sec/{doxm,pstat,acl,cred}) are deliberately
# EXCLUDED — those are the surfaces that nearly bricked the oven in
# prior sessions. The bridge has no reason to touch them.
# ---------------------------------------------------------------------
OBSERVE_PATHS = [
    ['operational', 'state', 'vs', '0'],    # state, time, progress
    ['power',       'vs', '0'],             # power On/Off
    ['oven',        'vs', '0'],             # cavity state (Cooking, Idle, …)
    ['temperatures','vs', '0'],             # current + desired temp
    ['doors',       'vs', '0'],             # openState
    ['kidslock',    'vs', '0'],             # child lock
    ['remotectrl',  'vs', '0'],             # remote control enabled
    ['mode',        'vs', '0'],             # cooking mode + options array
    ['alarms',      'vs', '0'],             # alarm code (OV_E_OFF etc.)
    ['connected',   'vs', '0'],             # cloud connectivity status
    ['otninformation', 'vs', '0'],          # firmware-update flags
]


# Setpoint bounds — union across modeSpec entries on this oven. Per-mode
# bounds (e.g. PlateWarm 30–80) tighten this; the firmware will refuse
# out-of-range writes for the active mode and the HA UI will surface
# the resulting 4.xx in the bridge log.
SETPOINT_MIN_C = 30
SETPOINT_MAX_C = 270
SETPOINT_STEP_C = 5


# Samsung's operational state strings → OCF currentMachineState shape.
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
    try: return float(v)
    except (TypeError, ValueError): return None


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _option_value(options, prefix, default=None):
    """Find `<prefix>_<value>` in an options array and return <value>."""
    for o in options:
        if o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return default


def _replace_in_options(options, prefix, new_value):
    """Return a new options array with any `<prefix>_*` entry replaced
    by `<prefix>_<new_value>`. Caller must verify `options` is the live
    options array first (Samsung uses replace-not-merge on this field)."""
    return [f"{prefix}_{new_value}" if o.startswith(prefix + '_') else o
            for o in options]


def _fmt_hms(seconds):
    """Format an integer second count as `H:MM:SS`. Returns None on
    bad input so callers can leave the field null rather than emitting
    a misleading `0:00:00`."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return None
    if s < 0:
        s = 0
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------
# flatten — Samsung /device/0 links → HA-flavoured sensor dict.
# Every field reads from `/<x>/vs/0` paths so push updates immediately
# drive every entity. Where a field is settable (lamp, mode, setpoint),
# we publish it as a read-side sensor here AND as a writeable entity
# in build_discovery; the read side closes the HA UI feedback loop.
# ---------------------------------------------------------------------
def flatten(links):
    g = lambda href, k, default=None: (links.get(href) or {}).get(k, default)

    # Operational
    sam_state = g('/operational/state/vs/0', 'x.com.samsung.da.state')
    machine_state = (_SAMSUNG_STATE_TO_OCF.get(sam_state, sam_state)
                     if sam_state is not None else None)

    operation_time = g('/operational/state/vs/0',
                       'x.com.samsung.da.operationTime')
    remaining = g('/operational/state/vs/0',
                  'x.com.samsung.da.remainingTime')
    rem_min = None
    if remaining:
        try:
            h, m, s = remaining.split(':')
            rem_min = int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
        except Exception:
            pass
    # operationTime parsed as minutes — the source of truth for "Cook
    # time" in HA (mid-cycle SmartThings updates land here, not in
    # /mode/vs/0 UpperTimerSet which is vestigial).
    op_min = None
    if operation_time:
        try:
            h, m, s = operation_time.split(':')
            op_min = int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
        except Exception:
            pass

    # Cavity state — Cooking, Idle, Preheating, …
    oven_state = g('/oven/vs/0', 'x.com.samsung.da.state')

    # Temperatures
    temps_items = (g('/temperatures/vs/0',
                     'x.com.samsung.da.items') or [])
    cur_c = des_c = None
    if temps_items:
        cur_c = _int(temps_items[0].get('x.com.samsung.da.current'))
        des_c = _int(temps_items[0].get('x.com.samsung.da.desired'))

    # Door
    doors_items = g('/doors/vs/0', 'x.com.samsung.da.items') or []
    door = doors_items[0].get('x.com.samsung.da.openState') if doors_items else None
    door_open = (door == 'Open') if door is not None else None

    # Power
    sam_power = g('/power/vs/0', 'x.com.samsung.da.power')
    power_bin = (sam_power == 'On') if sam_power is not None else None

    # Kidslock + Remote
    sam_kids = g('/kidslock/vs/0', 'x.com.samsung.da.kidsLock')
    kids_bin = (sam_kids != 'Ready') if sam_kids is not None else None
    sam_rc = g('/remotectrl/vs/0',
               'x.com.samsung.da.remoteControlEnabled')
    rc_bin = (str(sam_rc).lower() == 'true') if sam_rc is not None else None

    # Mode + options
    modes = g('/mode/vs/0', 'x.com.samsung.da.modes') or []
    current_mode = modes[0] if modes else None
    options = g('/mode/vs/0', 'x.com.samsung.da.options') or []
    lamp = _option_value(options, 'UpperLamp')          # 'On' / 'Off'
    sound = _option_value(options, 'Sound')             # 'On' / 'Off'
    fastpreheat = _option_value(options, 'fastpreheat') # 'On' / 'Off'
    # NaturalSteam only appears in the options array after it's been
    # touched in the SmartThings app at least once. Until then it's
    # absent, so `_option_value` returns None — surface that as None
    # (HA renders as "Unknown") rather than "Off", which would falsely
    # imply we know it's disabled.
    natural_steam = _option_value(options, 'NaturalSteam')  # 'On' / 'Off' / None
    timer_state = _option_value(options, 'UpperTimerState')   # 'Ready' / 'Running'
    # UpperTimerCurrent/UpperTimerSet are integer seconds. Format as
    # H:MM:SS for HA display so users see "1:10:00", not "4200".
    timer_current_raw = _option_value(options, 'UpperTimerCurrent')
    timer_set_raw = _option_value(options, 'UpperTimerSet')
    timer_current = _fmt_hms(timer_current_raw)
    timer_set = _fmt_hms(timer_set_raw)
    timer_current_seconds = _int(timer_current_raw)
    timer_set_seconds = _int(timer_set_raw)

    # Alarms
    alarm_items = g('/alarms/vs/0', 'x.com.samsung.da.items') or []
    alarm_code = (alarm_items[0].get('x.com.samsung.da.code')
                  if alarm_items else None)
    alarm_time = (alarm_items[0].get('x.com.samsung.da.triggeredTime')
                  if alarm_items else None)
    # OV_E_OFF appears when the oven is off / no alarm; treat as inactive.
    alarm_active = bool(alarm_code) and alarm_code != 'OV_E_OFF'

    # Connectivity / firmware
    sam_connected = g('/connected/vs/0', 'x.com.samsung.da.connected')
    connected_bin = (sam_connected == 'On') if sam_connected is not None else None
    fw_update_available = g('/otninformation/vs/0',
                            'x.com.samsung.da.newVersionAvailable')
    fw_update_bin = (str(fw_update_available).lower() == 'true'
                     if fw_update_available is not None else None)

    return {
        'machine_state':           machine_state,
        # `cycle_active` gates the writable controls in HA. The oven
        # only honours setpoint / cook-time / option writes (and Stop)
        # while a cycle is active — outside an active cycle, writes
        # return 2.04 but get rolled back within ~3s.
        'cycle_active':            machine_state == 'active',
        'oven_state':              oven_state,
        'progress_percentage':     _int(g('/operational/state/vs/0',
                                          'x.com.samsung.da.progressPercentage')),
        'operation_time':          operation_time,
        'operation_time_minutes':  op_min,
        'completion_time':         remaining,
        'completion_minutes':      rem_min,
        'current_temp_c':          cur_c,
        'target_temp_c':           des_c,
        'door':                    door,
        'door_open':               door_open,
        'power_state':             sam_power,
        'power_state_binary':      power_bin,
        'child_lock':              sam_kids,
        'child_lock_binary':       kids_bin,
        'remote_control':          sam_rc,
        'remote_control_binary':   rc_bin,
        'mode':                    current_mode,
        'lamp':                    lamp,
        'sound':                   sound,
        'fastpreheat':             fastpreheat,
        'natural_steam':           natural_steam,
        'timer_state':             timer_state,
        'timer_current':           timer_current,
        'timer_set':               timer_set,
        'timer_current_seconds':   timer_current_seconds,
        'timer_set_seconds':       timer_set_seconds,
        'alarm_code':              alarm_code,
        'alarm_time':              alarm_time,
        'alarm_active':            alarm_active,
        'connected':               sam_connected,
        'connected_binary':        connected_bin,
        'firmware_update_available': fw_update_bin,
    }


# ---------------------------------------------------------------------
# Remaining-time anchor + projection. The oven pushes /operational/state
# on state transitions but probably not on remainingTime ticks (matches
# dryer behaviour). Capture (ts, total_seconds) at each push and
# extrapolate downward while machine_state == active.
# ---------------------------------------------------------------------
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
            f"oven={sensors.get('oven_state')} "
            f"temp={sensors.get('current_temp_c')}/"
            f"{sensors.get('target_temp_c')}°C "
            f"mode={sensors.get('mode')} "
            f"timer_set={sensors.get('timer_set_seconds')} "
            f"timer_cur={sensors.get('timer_current_seconds')}")


# ---------------------------------------------------------------------
# HA discovery inventory
# ---------------------------------------------------------------------
MODEL = 'OCF oven (TizenRT-iotivity, NV7000BS-class)'

# (key, friendly name, extra config)
#
# Only read-only sensors live here. Fields that ALSO have an
# interactive entity (light, switch, number, select) are removed —
# the interactive entity already surfaces the live state, so a
# duplicate read-only "Lamp state" / "Fast preheat state" / etc.
# sensor would just clutter the device card with the same value
# twice.
_SENSORS = [
    ('machine_state',       'Machine state',        {'icon': 'mdi:stove'}),
    ('oven_state',          'Cavity state',         {}),
    # Cooking mode is read-only via local OCF — the oven owns the
    # `modes` field once a cycle is active and rolls back any writes.
    ('mode',                'Cooking mode',         {'icon': 'mdi:tune'}),
    ('progress_percentage', 'Progress percent',
        {'unit_of_measurement': '%', 'state_class': 'measurement'}),
    ('operation_time',      'Elapsed time',         {'icon': 'mdi:timer'}),
    ('completion_time',     'Completion time',      {'icon': 'mdi:timer-sand'}),
    ('completion_minutes',  'Remaining minutes',
        {'unit_of_measurement': 'min', 'device_class': 'duration',
         'state_class': 'measurement'}),
    ('current_temp_c',      'Temperature',
        {'unit_of_measurement': '°C', 'device_class': 'temperature',
         'state_class': 'measurement'}),
    # target_temp_c is also exposed as a Number entity for editing,
    # but the Number is RC-gated. The sensor stays always-visible so
    # the user can see the current setpoint even with Remote Control
    # off at the oven.
    ('target_temp_c',       'Setpoint',
        {'unit_of_measurement': '°C', 'device_class': 'temperature',
         'state_class': 'measurement', 'icon': 'mdi:thermometer-chevron-up'}),
    # power_state: read-only. The oven doesn't expose a meaningful
    # POST /power/vs/0 from cold — turning the unit on at the panel
    # is a physical action — so we don't ship a Power switch entity.
    ('power_state',         'Power state',          {'icon': 'mdi:power'}),
    ('door',                'Door state',           {}),
    ('child_lock',          'Child lock state',     {}),
    ('remote_control',      'Remote control state', {}),
    ('timer_state',         'Timer state',          {}),
    ('timer_current',       'Timer remaining',      {'icon': 'mdi:timer-sand'}),
    ('timer_set',           'Timer set',            {'icon': 'mdi:timer'}),
    ('alarm_code',          'Alarm code',
        {'icon': 'mdi:alert', 'entity_category': 'diagnostic'}),
    ('alarm_time',          'Alarm time',
        {'icon': 'mdi:clock-alert', 'entity_category': 'diagnostic'}),
    ('connected',           'Cloud connectivity',
        {'entity_category': 'diagnostic'}),
]

# (key, friendly, value_template, device_class, extras)
_BINARY_SENSORS = [
    ('running', 'Running',
        "{{ 'ON' if value_json.machine_state == 'active' else 'OFF' }}",
        'running', {}),
    ('door_open', 'Door',
        "{{ 'ON' if value_json.door_open else 'OFF' }}",
        'door', {}),
    # `power_switch` binary_sensor would duplicate the Power switch
    # entity below; the switch already shows on/off state.
    ('child_lock_active', 'Child lock',
        "{{ 'ON' if value_json.child_lock_binary else 'OFF' }}",
        'lock', {}),
    ('remote_control_enabled', 'Remote control',
        "{{ 'ON' if value_json.remote_control_binary else 'OFF' }}",
        'connectivity', {}),
    ('alarm_active', 'Alarm active',
        "{{ 'ON' if value_json.alarm_active else 'OFF' }}",
        'problem', {}),
    ('connected_bin', 'Connected',
        "{{ 'ON' if value_json.connected_binary else 'OFF' }}",
        'connectivity', {'entity_category': 'diagnostic'}),
    ('firmware_update_available', 'Firmware update available',
        "{{ 'ON' if value_json.firmware_update_available else 'OFF' }}",
        'update', {'entity_category': 'diagnostic'}),
]


# MQTT command-topic suffixes (under <prefix>/cmd/…)
CMD_LAMP         = 'cmd/lamp'
CMD_SOUND        = 'cmd/sound'
CMD_FASTPREHEAT  = 'cmd/fastpreheat'
CMD_NATURALSTEAM = 'cmd/naturalsteam'
CMD_POWER        = 'cmd/power'
CMD_STOP         = 'cmd/stop'
CMD_SETPOINT     = 'cmd/setpoint'
CMD_COOK_TIME    = 'cmd/cook_time'
# NOTE — no CMD_START or CMD_MODE. Reverse-engineered 2026-05-31:
#   * `state='Run'` writes to /operational/state/vs/0 are accepted
#     (2.04) and machine briefly goes active, but the oven cavity
#     stays Ready (no Preheat) and the cycle self-cancels within
#     ~3s. Tried every byte-level approximation of SmartThings's
#     working start (matching all four fields on /operational/state,
#     +operationTime, +remainingTime, +progressPercentage='1', plus
#     /temperatures/vs/0 desired, with and without /mode/vs/0 modes,
#     with PUT vs POST, paced 1s apart, with OCF-version-options
#     2049/2053, with Samsung vendor-option 65524=0xc0) — none of
#     these engage the cavity. The differentiator must be something
#     invisible at the OBSERVE-push level (likely a cloud-mediated
#     auth path the SmartThings app uses). See project_oven_remote
#     _start_open.md for full notes.
#   * `modes=['Convection']` writes to /mode/vs/0 succeed (2.04)
#     but the oven owns the field once a cycle is active and rolls
#     local writes back to ['NoOperation']. mode is surfaced as a
#     read-only sensor instead.


def build_discovery(topic_prefix, ha_prefix, device_name):
    state_topic   = f"{topic_prefix}/state"
    avail_topic   = f"{topic_prefix}/availability"
    remote_topic  = f"{topic_prefix}/remote_available"
    cycle_topic   = f"{topic_prefix}/cycle_active"
    dev = device_block(topic_prefix, device_name, MODEL)
    out = []

    # --- read-only sensors -------------------------------------------
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

    for key, name, template, dclass, extra in _BINARY_SENSORS:
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
        cfg.update(extra)
        out.append((f"{ha_prefix}/binary_sensor/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # --- light: oven lamp (proven via probe_oven_lamp_states.py;
    # binary On/Off only — High/Low/Dim coerce back to previous
    # state. Works regardless of Remote Control switch, so we only
    # gate on base availability). For the MQTT light default schema,
    # state_value_template's output must match payload_on/payload_off
    # exactly (case-sensitive) for HA to recognise the state.
    cfg = {
        'name':                 'Lamp',
        'unique_id':            f"{topic_prefix}_lamp_light",
        'object_id':            f"{topic_prefix}_lamp_light",
        'state_topic':          state_topic,
        'state_value_template': "{{ value_json.lamp }}",
        'command_topic':        f"{topic_prefix}/{CMD_LAMP}",
        'payload_on':           'On',
        'payload_off':          'Off',
        'icon':                 'mdi:track-light',
        'availability':         avail_base(avail_topic),
        'device':               dev,
    }
    out.append((f"{ha_prefix}/light/{topic_prefix}/lamp/config",
                encode(cfg)))

    # --- switches. Sound is always-available — independent of cycle
    # state, no RC required. Fast preheat + Natural steam are
    # options-array writes the oven only honours mid-cycle, so they
    # gate on RC + cycle_active. Power deliberately omitted: turning
    # the oven on is a physical-panel action; read-only power_state
    # sensor reflects its state.
    cfg = {
        'name':           'Sound',
        'unique_id':      f"{topic_prefix}_sound_switch",
        'object_id':      f"{topic_prefix}_sound_switch",
        'state_topic':    state_topic,
        'value_template': '{{ value_json.sound }}',
        'state_on':       'On',
        'state_off':      'Off',
        'command_topic':  f"{topic_prefix}/{CMD_SOUND}",
        'payload_on':     'On',
        'payload_off':    'Off',
        'icon':           'mdi:volume-high',
        'availability':   avail_base(avail_topic),
        'device':         dev,
    }
    out.append((f"{ha_prefix}/switch/{topic_prefix}/sound/config",
                encode(cfg)))

    cycle_switches = [
        ('fastpreheat',   'Fast preheat',  '{{ value_json.fastpreheat }}',   CMD_FASTPREHEAT,  'mdi:fire'),
        ('natural_steam', 'Natural steam', '{{ value_json.natural_steam }}', CMD_NATURALSTEAM, 'mdi:kettle-steam'),
    ]
    for key, name, tpl, cmd, icon in cycle_switches:
        cfg = {
            'name':              name,
            'unique_id':         f"{topic_prefix}_{key}_switch",
            'object_id':         f"{topic_prefix}_{key}_switch",
            'state_topic':       state_topic,
            'value_template':    tpl,
            'state_on':          'On',
            'state_off':         'Off',
            'command_topic':     f"{topic_prefix}/{cmd}",
            'payload_on':        'On',
            'payload_off':       'Off',
            'icon':              icon,
            'availability':      avail_with_remote_and_cycle(
                                     avail_topic, remote_topic, cycle_topic),
            'availability_mode': 'all',
            'device':            dev,
        }
        out.append((f"{ha_prefix}/switch/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # --- number: setpoint (RC-gated, slider input) ------------------
    cfg = {
        'name':              'Setpoint',
        'unique_id':         f"{topic_prefix}_setpoint",
        'object_id':         f"{topic_prefix}_setpoint",
        'state_topic':       state_topic,
        'value_template':    '{{ value_json.target_temp_c }}',
        'command_topic':     f"{topic_prefix}/{CMD_SETPOINT}",
        'min':               SETPOINT_MIN_C,
        'max':               SETPOINT_MAX_C,
        'step':              SETPOINT_STEP_C,
        'unit_of_measurement': '°C',
        'device_class':      'temperature',
        'mode':              'slider',
        'icon':              'mdi:thermometer-chevron-up',
        # RC + cycle_active gated — Samsung's local-OCF surface only
        # honours setpoint changes while a cycle is actually running
        # (idle writes get rolled back within ~3s).
        'availability':      avail_with_remote_and_cycle(
                                 avail_topic, remote_topic, cycle_topic),
        'availability_mode': 'all',
        'device':            dev,
    }
    out.append((f"{ha_prefix}/number/{topic_prefix}/setpoint/config",
                encode(cfg)))

    # --- button: Stop cycle ----------------------------------------
    # Gated on cycle_active — there's nothing to stop when idle.
    # There is no Start button: local-OCF cycle start is not
    # reproducible on this firmware (see project_oven_remote_start
    # _open.md memory note for the full investigation). Cooking mode
    # is similarly omitted — read-only via local OCF, surfaced as a
    # sensor.
    cfg = {
        'name':              'Stop cycle',
        'unique_id':         f"{topic_prefix}_stop",
        'object_id':         f"{topic_prefix}_stop",
        'command_topic':     f"{topic_prefix}/{CMD_STOP}",
        'payload_press':     'Stop',
        'icon':              'mdi:stop',
        'availability':      avail_with_cycle(avail_topic, cycle_topic),
        'availability_mode': 'all',
        'device':            dev,
    }
    out.append((f"{ha_prefix}/button/{topic_prefix}/stop/config",
                encode(cfg)))

    # --- number: Cook time in minutes (RC + cycle gated; the oven
    # only honours operationTime writes while running). Source of
    # truth is `operationTime` on /operational/state/vs/0;
    # SmartThings's mid-cycle time changes land in that same field.
    cfg = {
        'name':              'Cook time',
        'unique_id':         f"{topic_prefix}_cook_time",
        'object_id':         f"{topic_prefix}_cook_time",
        'state_topic':       state_topic,
        'value_template':    '{{ value_json.operation_time_minutes | int(0) }}',
        'command_topic':     f"{topic_prefix}/{CMD_COOK_TIME}",
        'min':               0,
        'max':               1439,    # 23:59 — matches modeSpec timeMax
        'step':              1,
        'unit_of_measurement': 'min',
        'mode':              'box',
        'icon':              'mdi:timer',
        'availability':      avail_with_remote_and_cycle(
                                 avail_topic, remote_topic, cycle_topic),
        'availability_mode': 'all',
        'device':            dev,
    }
    out.append((f"{ha_prefix}/number/{topic_prefix}/cook_time/config",
                encode(cfg)))

    # --- removal: publish empty payload to the discovery topics of
    # entities we used to expose. HA treats an empty retained payload
    # on a discovery topic as "delete this entity", so previously-set
    # up Start buttons and Cooking-mode selects disappear cleanly.
    out.append((f"{ha_prefix}/button/{topic_prefix}/start/config", b''))
    out.append((f"{ha_prefix}/select/{topic_prefix}/mode/config",  b''))

    return out


# ---------------------------------------------------------------------
# Command handlers — fn(payload, links) → (path_segs, body_dict) | None.
# Read-modify-write handlers (lamp/sound/fastpreheat) snapshot the
# `/mode/vs/0` options array and replace just their slot. /temperatures
# is also RMW because Samsung's write semantics on the items array are
# replace-not-merge.
# ---------------------------------------------------------------------
def _mode_options(links):
    """Return the live `/mode/vs/0` options array (a copy), or None
    if /mode/vs/0 isn't seeded yet."""
    rep = links.get('/mode/vs/0') or {}
    opts = rep.get('x.com.samsung.da.options')
    if not opts:
        return None
    return list(opts)


def _temps_items(links):
    """Return a deep-ish copy of the /temperatures/vs/0 items array."""
    rep = links.get('/temperatures/vs/0') or {}
    items = rep.get('x.com.samsung.da.items') or []
    return [dict(it) for it in items] if items else None


def command_handlers():
    def _lamp(p, links):
        if p not in ('On', 'Off'):
            return None
        opts = _mode_options(links)
        if opts is None:
            return None
        return ['mode', 'vs', '0'], {
            'x.com.samsung.da.options': _replace_in_options(opts, 'UpperLamp', p),
        }

    def _sound(p, links):
        if p not in ('On', 'Off'):
            return None
        opts = _mode_options(links)
        if opts is None:
            return None
        return ['mode', 'vs', '0'], {
            'x.com.samsung.da.options': _replace_in_options(opts, 'Sound', p),
        }

    def _fastpreheat(p, links):
        if p not in ('On', 'Off'):
            return None
        opts = _mode_options(links)
        if opts is None:
            return None
        return ['mode', 'vs', '0'], {
            'x.com.samsung.da.options': _replace_in_options(
                opts, 'fastpreheat', p),
        }

    def _naturalsteam(p, links):
        # NaturalSteam_* only appears in the options array after the
        # SmartThings app has touched it once. If absent, append the
        # slot — the oven creates it on first write, so the bridge
        # doesn't need a "prime via app" dance.
        if p not in ('On', 'Off'):
            return None
        opts = _mode_options(links)
        if opts is None:
            return None
        if not any(o.startswith('NaturalSteam_') for o in opts):
            opts = opts + [f'NaturalSteam_{p}']
        else:
            opts = _replace_in_options(opts, 'NaturalSteam', p)
        return ['mode', 'vs', '0'], {
            'x.com.samsung.da.options': opts,
        }

    def _power(p, _links):
        if p not in ('On', 'Off'):
            return None
        return ['power', 'vs', '0'], {'x.com.samsung.da.power': p}

    def _stop(_p, _links):
        return ['operational', 'state', 'vs', '0'], {
            'x.com.samsung.da.state': 'Ready',
        }

    def _setpoint(p, links):
        try:
            temp = float(p)
        except (TypeError, ValueError):
            return None
        temp_i = int(round(temp / SETPOINT_STEP_C) * SETPOINT_STEP_C)
        if not (SETPOINT_MIN_C <= temp_i <= SETPOINT_MAX_C):
            return None
        items = _temps_items(links)
        if items is None:
            return None
        items[0]['x.com.samsung.da.desired'] = str(temp_i)
        return ['temperatures', 'vs', '0'], {
            'x.com.samsung.da.items': items,
        }

    def _cook_time(p, links):
        # HA sends minutes; oven cycle duration lives in
        # /operational/state/vs/0 as `operationTime` / `remainingTime`
        # (H:MM:SS strings). Writing both — mirroring SmartThings's
        # observed behaviour, which resets the live countdown to the
        # new duration. Clamp to modeSpec's 0..23:59.
        try:
            minutes = int(round(float(p)))
        except (TypeError, ValueError):
            return None
        if not (0 <= minutes <= 1439):
            return None
        h, m = divmod(minutes, 60)
        hms = f"{h:02d}:{m:02d}:00"
        return ['operational', 'state', 'vs', '0'], {
            'x.com.samsung.da.operationTime': hms,
            'x.com.samsung.da.remainingTime': hms,
        }

    return {
        CMD_LAMP:         _lamp,
        CMD_SOUND:        _sound,
        CMD_FASTPREHEAT:  _fastpreheat,
        CMD_NATURALSTEAM: _naturalsteam,
        CMD_POWER:        _power,
        CMD_STOP:         _stop,
        CMD_SETPOINT:     _setpoint,
        CMD_COOK_TIME:    _cook_time,
    }


# ---------------------------------------------------------------------
OVEN = ApplianceDescriptor(
    name='oven',
    default_observe_port=49154,
    observe_paths=OBSERVE_PATHS,
    seed_path=['device', '0'],
    flatten=flatten,
    build_discovery=build_discovery,
    command_handlers=command_handlers,
    on_observation=on_observation,
    project=project,
    remote_available_field='remote_control_binary',
    cycle_active_field='cycle_active',
    log_state_change=log_state_change,
)

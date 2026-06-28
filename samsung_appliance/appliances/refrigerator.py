"""Refrigerator descriptor (Samsung RF9000B-class / TP1X_REF_21K).

Resource map captured 2026-06-27 via DTLS-CoAP with the ab0b0ac4 cert
from a unit at 10.0.0.254:49154.
See local-tools/dumps/10.0.0.254.json for the full field reference.

This appliance has no "cycle" concept — there is no machine_state and
no remote-control gate. All controls are always available when the bridge
is online.

Temperature units: this model reports in Fahrenheit.
  Freezer setpoint range: -8°F to 5°F
  Fridge setpoint range:  34°F to 44°F

Write surfaces (all unproven — modelled on oven/dryer write semantics,
first HA interaction is the live test):
  * Freezer and fridge setpoints via /temperatures/vs/0 items RMW
    (same pattern as oven /temperatures/vs/0)
  * Rapid fridge / rapid freezing via /refrigeration/vs/0
  * Ice maker 1 (cubed) on/off via /icemaker/one/vs/0
  * Ice maker 2 (whiskey ball) type select via /icemaker/two/vs/0
  * Autofill via /autofill/vs/0
  * Cabinet light on/off via /cabinet/light/total/vs/0
  * Beverage zone mode select via /specialzone/one/vs/0
"""

from .base import (
    ApplianceDescriptor,
    avail_base,
    device_block,
    encode,
)
from ..poll_scheduler import PollTier


# --- OBSERVE paths --------------------------------------------------------
OBSERVE_PATHS = [
    ['doors',          'vs', '0'],               # door open states (2 doors)
    ['temperatures',   'vs', '0'],               # current + desired temp, both zones
    ['icemaker',       'status', 'vs', '0'],     # global ice maker enabled
    ['icemaker',       'one', 'vs', '0'],        # cubed ice maker state + type
    ['icemaker',       'two', 'vs', '0'],        # whiskey ball ice maker type
    ['refrigeration',  'vs', '0'],               # rapidFridge, rapidFreezing
    ['energy',         'consumption', 'vs', '0'],
    ['alarms',         'vs', '0'],
    ['cabinet',        'light', 'total', 'vs', '0'],
    ['autofill',       'vs', '0'],
    ['filter',         'waterfilter', 'vs', '0'],
    ['sabbath',        'vs', '0'],
    ['status',         'lock', 'vs', '0'],       # device sound + ADO control
]

# Setpoint bounds read from /temperatures/vs/0 items on 2026-06-27.
FREEZER_MIN_F = -8
FREEZER_MAX_F = 5
FRIDGE_MIN_F  = 34
FRIDGE_MAX_F  = 44

# Whiskey ball ice sizes available on this model.
ICE2_MODES = ['Off', 'WHISKEY_ICEBALL_3', 'WHISKEY_ICEBALL_6', 'WHISKEY_ICEBALL_9']

# Beverage zone flex modes on this model.
BZONE_MODES = ['SP_TTYPE_BEER_DRINKS', 'SP_TTYPE_WINE_DESSERT']


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- flatten --------------------------------------------------------------
def flatten(links):
    g = lambda href, k, default=None: (links.get(href) or {}).get(k, default)

    # Temperatures — items[0]=Freezer, items[1]=Fridge.
    temps_items = g('/temperatures/vs/0', 'x.com.samsung.da.items') or []
    freezer_cur = freezer_des = fridge_cur = fridge_des = None
    if len(temps_items) > 0:
        freezer_cur = _int(temps_items[0].get('x.com.samsung.da.current'))
        freezer_des = _int(temps_items[0].get('x.com.samsung.da.desired'))
    if len(temps_items) > 1:
        fridge_cur  = _int(temps_items[1].get('x.com.samsung.da.current'))
        fridge_des  = _int(temps_items[1].get('x.com.samsung.da.desired'))

    # Doors — items[0] and [1], openState 'Open'/'Close'.
    doors_items = g('/doors/vs/0', 'x.com.samsung.da.items') or []
    door0_open = door1_open = None
    if len(doors_items) > 0:
        s = doors_items[0].get('x.com.samsung.da.openState')
        door0_open = (s == 'Open') if s is not None else None
    if len(doors_items) > 1:
        s = doors_items[1].get('x.com.samsung.da.openState')
        door1_open = (s == 'Open') if s is not None else None
    any_door_open = bool(door0_open or door1_open)

    # Ice makers.
    ice_enabled = g('/icemaker/status/vs/0', 'x.com.samsung.da.iceMaker')
    ice1_state  = g('/icemaker/one/vs/0', 'x.com.samsung.da.iceMaker.state')
    ice1_status = g('/icemaker/one/vs/0', 'x.com.samsung.da.iceMaker.iceMakingStatus')
    ice1_on     = (ice1_state == 'On') if ice1_state is not None else None
    ice2_type   = g('/icemaker/two/vs/0', 'x.com.samsung.da.iceType.desired')
    ice2_status = g('/icemaker/two/vs/0', 'x.com.samsung.da.iceMaker.iceMakingStatus')

    # Modes.
    rapid_fridge   = g('/refrigeration/vs/0', 'x.com.samsung.da.rapidFridge')
    rapid_freezing = g('/refrigeration/vs/0', 'x.com.samsung.da.rapidFreezing')
    autofill       = g('/autofill/vs/0', 'x.com.samsung.da.autofill')
    sabbath        = g('/sabbath/vs/0', 'x.com.samsung.da.sabbathMode')

    # Cabinet light.
    light_control = g('/cabinet/light/total/vs/0', 'x.com.samsung.da.lightControl')
    light_on      = (light_control == 'On') if light_control is not None else None

    # Filter.
    filter_usage  = _int(g('/filter/waterfilter/vs/0', 'x.com.samsung.da.filterUsage'))
    filter_status = g('/filter/waterfilter/vs/0', 'x.com.samsung.da.filterStatus')

    # Beverage zone.
    bzone_rep  = links.get('/specialzone/one/vs/0') or {}
    bzone_mode = bzone_rep.get('roomDesiredMode')

    # Energy.
    inst_w  = _num(g('/energy/consumption/vs/0', 'x.com.samsung.da.instantaneousPower'))
    cum_wh  = _num(g('/energy/consumption/vs/0', 'x.com.samsung.da.cumulativePower'))

    # Alarms — empty array when nothing is active on this model.
    alarm_items = g('/alarms/vs/0', 'x.com.samsung.da.items') or []
    alarm_code  = (alarm_items[-1].get('x.com.samsung.da.code')
                   if alarm_items else None)

    # Firmware update.
    fw_update = g('/otninformation/vs/0', 'x.com.samsung.da.newVersionAvailable')
    fw_update_bin = (str(fw_update).lower() == 'true'
                     if fw_update is not None else None)

    return {
        'freezer_temp_f':        freezer_cur,
        'freezer_setpoint_f':    freezer_des,
        'fridge_temp_f':         fridge_cur,
        'fridge_setpoint_f':     fridge_des,
        'door_freezer_open':     door0_open,
        'door_fridge_open':      door1_open,
        'any_door_open':         any_door_open,
        'ice_maker_enabled':     ice_enabled,
        'ice1_state':            ice1_state,
        'ice1_on':               ice1_on,
        'ice1_making_status':    ice1_status,
        'ice2_type':             ice2_type,
        'ice2_making_status':    ice2_status,
        'rapid_fridge':          rapid_fridge,
        'rapid_freezing':        rapid_freezing,
        'autofill':              autofill,
        'sabbath_mode':          sabbath,
        'cabinet_light':         light_control,
        'cabinet_light_on':      light_on,
        'filter_usage':          filter_usage,
        'filter_status':         filter_status,
        'beverage_zone_mode':    bzone_mode,
        'power_watts':           max(0.0, inst_w) if inst_w is not None else None,
        'energy_kwh':            round(cum_wh / 1000.0, 2)
                                   if cum_wh is not None else None,
        'alarm_code':            alarm_code,
        'firmware_update':       fw_update_bin,
    }


def log_state_change(sensors):
    return (f"freezer={sensors.get('freezer_temp_f')}°F "
            f"fridge={sensors.get('fridge_temp_f')}°F "
            f"door={'open' if sensors.get('any_door_open') else 'closed'} "
            f"power={sensors.get('power_watts')}W")


# --- HA discovery ---------------------------------------------------------
MODEL = 'OCF refrigerator (TizenRT-iotivity, RF9000B-class)'

_SENSORS = [
    ('freezer_temp_f',     'Freezer temperature',
        {'unit_of_measurement': '°F', 'device_class': 'temperature',
         'state_class': 'measurement'}),
    ('freezer_setpoint_f', 'Freezer setpoint',
        {'unit_of_measurement': '°F', 'device_class': 'temperature',
         'icon': 'mdi:thermometer-chevron-down'}),
    ('fridge_temp_f',      'Fridge temperature',
        {'unit_of_measurement': '°F', 'device_class': 'temperature',
         'state_class': 'measurement'}),
    ('fridge_setpoint_f',  'Fridge setpoint',
        {'unit_of_measurement': '°F', 'device_class': 'temperature',
         'icon': 'mdi:thermometer-chevron-up'}),
    ('ice1_making_status', 'Ice maker (cubed) status',  {'icon': 'mdi:cube'}),
    ('ice2_making_status', 'Ice maker (whiskey) status', {'icon': 'mdi:circle'}),
    ('filter_usage',       'Water filter usage',
        {'unit_of_measurement': '%', 'state_class': 'measurement',
         'icon': 'mdi:filter'}),
    ('filter_status',      'Water filter status',        {'icon': 'mdi:filter-check'}),
    ('power_watts',        'Power',
        {'unit_of_measurement': 'W', 'device_class': 'power',
         'state_class': 'measurement'}),
    ('energy_kwh',         'Energy',
        {'unit_of_measurement': 'kWh', 'device_class': 'energy',
         'state_class': 'total_increasing'}),
    ('alarm_code',         'Alarm code',
        {'icon': 'mdi:alert', 'entity_category': 'diagnostic'}),
]

_BINARY_SENSORS = [
    ('door_freezer_open', 'Freezer door',
        "{{ 'ON' if value_json.door_freezer_open else 'OFF' }}", 'door'),
    ('door_fridge_open',  'Fridge door',
        "{{ 'ON' if value_json.door_fridge_open else 'OFF' }}", 'door'),
    ('ice1_on', 'Ice maker (cubed)',
        "{{ 'ON' if value_json.ice1_on else 'OFF' }}", 'running'),
    ('firmware_update', 'Firmware update available',
        "{{ 'ON' if value_json.firmware_update else 'OFF' }}", 'update'),
]

CMD_FREEZER_SETPOINT = 'cmd/freezer_setpoint'
CMD_FRIDGE_SETPOINT  = 'cmd/fridge_setpoint'
CMD_RAPID_FRIDGE     = 'cmd/rapid_fridge'
CMD_RAPID_FREEZING   = 'cmd/rapid_freezing'
CMD_ICE1             = 'cmd/ice1'
CMD_ICE2_TYPE        = 'cmd/ice2_type'
CMD_AUTOFILL         = 'cmd/autofill'
CMD_CABINET_LIGHT    = 'cmd/cabinet_light'
CMD_BZONE_MODE       = 'cmd/beverage_zone_mode'


def build_discovery(topic_prefix, ha_prefix, device_name):
    state_topic = f"{topic_prefix}/state"
    avail_topic = f"{topic_prefix}/availability"
    dev = device_block(topic_prefix, device_name, MODEL)
    avail = avail_base(avail_topic)
    out = []

    for key, name, extra in _SENSORS:
        cfg = {
            'name':           name,
            'unique_id':      f"{topic_prefix}_{key}",
            'object_id':      f"{topic_prefix}_{key}",
            'state_topic':    state_topic,
            'value_template': f"{{{{ value_json.{key} }}}}",
            'availability':   avail,
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
            'availability':   avail,
            'device':         dev,
        }
        out.append((f"{ha_prefix}/binary_sensor/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # number: freezer setpoint
    cfg = {
        'name':              'Freezer setpoint',
        'unique_id':         f"{topic_prefix}_freezer_setpoint_num",
        'object_id':         f"{topic_prefix}_freezer_setpoint_num",
        'state_topic':       state_topic,
        'value_template':    '{{ value_json.freezer_setpoint_f }}',
        'command_topic':     f"{topic_prefix}/{CMD_FREEZER_SETPOINT}",
        'min':               FREEZER_MIN_F,
        'max':               FREEZER_MAX_F,
        'step':              1,
        'unit_of_measurement': '°F',
        'device_class':      'temperature',
        'mode':              'slider',
        'icon':              'mdi:thermometer-chevron-down',
        'availability':      avail,
        'device':            dev,
    }
    out.append((f"{ha_prefix}/number/{topic_prefix}/freezer_setpoint/config",
                encode(cfg)))

    # number: fridge setpoint
    cfg = {
        'name':              'Fridge setpoint',
        'unique_id':         f"{topic_prefix}_fridge_setpoint_num",
        'object_id':         f"{topic_prefix}_fridge_setpoint_num",
        'state_topic':       state_topic,
        'value_template':    '{{ value_json.fridge_setpoint_f }}',
        'command_topic':     f"{topic_prefix}/{CMD_FRIDGE_SETPOINT}",
        'min':               FRIDGE_MIN_F,
        'max':               FRIDGE_MAX_F,
        'step':              1,
        'unit_of_measurement': '°F',
        'device_class':      'temperature',
        'mode':              'slider',
        'icon':              'mdi:thermometer-chevron-up',
        'availability':      avail,
        'device':            dev,
    }
    out.append((f"{ha_prefix}/number/{topic_prefix}/fridge_setpoint/config",
                encode(cfg)))

    # switches: rapid fridge, rapid freezing, autofill, cabinet light, ice maker 1
    for key, name, tpl, cmd, icon in [
        ('rapid_fridge',   'Rapid fridge',    '{{ value_json.rapid_fridge }}',
         CMD_RAPID_FRIDGE,   'mdi:fridge-industrial'),
        ('rapid_freezing', 'Rapid freezing',  '{{ value_json.rapid_freezing }}',
         CMD_RAPID_FREEZING, 'mdi:snowflake'),
        ('autofill',       'Autofill',        '{{ value_json.autofill }}',
         CMD_AUTOFILL,       'mdi:cup-water'),
        ('cabinet_light',  'Cabinet light',   '{{ value_json.cabinet_light }}',
         CMD_CABINET_LIGHT,  'mdi:fridge-outline'),
        ('ice1_switch',    'Ice maker (cubed)', '{{ value_json.ice1_state }}',
         CMD_ICE1,           'mdi:cube'),
    ]:
        cfg = {
            'name':           name,
            'unique_id':      f"{topic_prefix}_{key}_switch",
            'object_id':      f"{topic_prefix}_{key}_switch",
            'state_topic':    state_topic,
            'value_template': tpl,
            'state_on':       'On',
            'state_off':      'Off',
            'command_topic':  f"{topic_prefix}/{cmd}",
            'payload_on':     'On',
            'payload_off':    'Off',
            'icon':           icon,
            'availability':   avail,
            'device':         dev,
        }
        out.append((f"{ha_prefix}/switch/{topic_prefix}/{key}/config",
                    encode(cfg)))

    # select: whiskey ball ice type
    cfg = {
        'name':           'Whiskey ball ice type',
        'unique_id':      f"{topic_prefix}_ice2_type_select",
        'object_id':      f"{topic_prefix}_ice2_type_select",
        'state_topic':    state_topic,
        'value_template': '{{ value_json.ice2_type }}',
        'command_topic':  f"{topic_prefix}/{CMD_ICE2_TYPE}",
        'options':        ICE2_MODES,
        'icon':           'mdi:circle',
        'availability':   avail,
        'device':         dev,
    }
    out.append((f"{ha_prefix}/select/{topic_prefix}/ice2_type/config",
                encode(cfg)))

    # select: beverage zone mode
    cfg = {
        'name':           'Beverage zone mode',
        'unique_id':      f"{topic_prefix}_bzone_mode_select",
        'object_id':      f"{topic_prefix}_bzone_mode_select",
        'state_topic':    state_topic,
        'value_template': '{{ value_json.beverage_zone_mode }}',
        'command_topic':  f"{topic_prefix}/{CMD_BZONE_MODE}",
        'options':        BZONE_MODES,
        'icon':           'mdi:glass-wine',
        'availability':   avail,
        'device':         dev,
    }
    out.append((f"{ha_prefix}/select/{topic_prefix}/bzone_mode/config",
                encode(cfg)))

    return out


# --- Command handlers -----------------------------------------------------
def _temps_items(links):
    rep = links.get('/temperatures/vs/0') or {}
    items = rep.get('x.com.samsung.da.items') or []
    return [dict(it) for it in items] if items else None


def command_handlers():
    def _freezer_setpoint(p, links):
        try:
            temp = int(round(float(p)))
        except (TypeError, ValueError):
            return None
        if not (FREEZER_MIN_F <= temp <= FREEZER_MAX_F):
            return None
        items = _temps_items(links)
        if not items:
            return None
        items[0]['x.com.samsung.da.desired'] = str(temp)
        return ['temperatures', 'vs', '0'], {'x.com.samsung.da.items': items}

    def _fridge_setpoint(p, links):
        try:
            temp = int(round(float(p)))
        except (TypeError, ValueError):
            return None
        if not (FRIDGE_MIN_F <= temp <= FRIDGE_MAX_F):
            return None
        items = _temps_items(links)
        if not items or len(items) < 2:
            return None
        items[1]['x.com.samsung.da.desired'] = str(temp)
        return ['temperatures', 'vs', '0'], {'x.com.samsung.da.items': items}

    def _on_off(key, path_segs):
        def _handler(p, _links):
            if p not in ('On', 'Off'):
                return None
            return path_segs, {key: p}
        return _handler

    def _ice1(p, _links):
        if p not in ('On', 'Off'):
            return None
        return ['icemaker', 'one', 'vs', '0'], {
            'x.com.samsung.da.iceMaker.state': p,
        }

    def _ice2_type(p, _links):
        if p not in ICE2_MODES:
            return None
        return ['icemaker', 'two', 'vs', '0'], {
            'x.com.samsung.da.iceType.desired': p,
        }

    def _bzone_mode(p, _links):
        if p not in BZONE_MODES:
            return None
        return ['specialzone', 'one', 'vs', '0'], {'roomDesiredMode': p}

    return {
        CMD_FREEZER_SETPOINT: _freezer_setpoint,
        CMD_FRIDGE_SETPOINT:  _fridge_setpoint,
        CMD_RAPID_FRIDGE:     _on_off('x.com.samsung.da.rapidFridge',
                                      ['refrigeration', 'vs', '0']),
        CMD_RAPID_FREEZING:   _on_off('x.com.samsung.da.rapidFreezing',
                                      ['refrigeration', 'vs', '0']),
        CMD_ICE1:             _ice1,
        CMD_ICE2_TYPE:        _ice2_type,
        CMD_AUTOFILL:         _on_off('x.com.samsung.da.autofill',
                                      ['autofill', 'vs', '0']),
        CMD_CABINET_LIGHT:    _on_off('x.com.samsung.da.lightControl',
                                      ['cabinet', 'light', 'total', 'vs', '0']),
        CMD_BZONE_MODE:       _bzone_mode,
    }


# --- Poll tiers -----------------------------------------------------------
# No empirical ceiling measurement on this device yet. Using a conservative
# ~8 req/s budget (matching oven) until a probe is run.
# Doors on the hot tier for sub-second open/close alerts.
FRIDGE_POLL_TIERS = [
    PollTier(
        name='hot',
        interval_s=1.0,
        paths=(
            ('doors', 'vs', '0'),
        ),
    ),
    PollTier(
        name='warm',
        interval_s=30.0,
        paths=(
            ('temperatures',   'vs', '0'),
            ('icemaker',       'status', 'vs', '0'),
            ('icemaker',       'one', 'vs', '0'),
            ('icemaker',       'two', 'vs', '0'),
            ('refrigeration',  'vs', '0'),
            ('energy',         'consumption', 'vs', '0'),
            ('filter',         'waterfilter', 'vs', '0'),
            ('autofill',       'vs', '0'),
            ('cabinet',        'light', 'total', 'vs', '0'),
            ('sabbath',        'vs', '0'),
            ('status',         'lock', 'vs', '0'),
            ('specialzone',    'one', 'vs', '0'),
            ('alarms',         'vs', '0'),
        ),
    ),
    PollTier(
        name='cold',
        interval_s=600.0,
        paths=(
            ('otninformation', 'vs', '0'),
        ),
    ),
    PollTier(
        name='sweep',
        interval_s=600.0,
        paths=(('device', '0'),),
        is_sweep=True,
    ),
]


# --- Descriptor -----------------------------------------------------------
REFRIGERATOR = ApplianceDescriptor(
    name='refrigerator',
    default_observe_port=49154,
    observe_paths=OBSERVE_PATHS,
    seed_path=['device', '0'],
    flatten=flatten,
    build_discovery=build_discovery,
    command_handlers=command_handlers,
    log_state_change=log_state_change,
    poll_tiers=FRIDGE_POLL_TIERS,
    # No remote_available_field — fridge has no remote-control gate.
    # No cycle_active_field — fridge has no cycle concept.
    # No is_active — always uses normal poll interval.
)

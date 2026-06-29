"""Unit tests for oven-family capabilities and the cycle-active adapter wiring."""
from samsung_appliance.registry.capabilities import oven
from samsung_appliance.registry.adapter import build_runtime_descriptor
from samsung_appliance.registry.discovery import discover


# ---------------------------------------------------------------------------
# OVEN_SETPOINT — NumberDesc with RMW write semantics
# ---------------------------------------------------------------------------

def test_oven_setpoint_write_is_read_modify_write():
    cap = oven.OVEN_SETPOINT
    desc = cap.entities[0]
    rep = {'x.com.samsung.da.items': [
        {'x.com.samsung.da.id': 'Target', 'x.com.samsung.da.temperature': 0}]}
    path, body = desc.write_fn(200, rep)
    assert path[-1] == '0'   # writes back to the resource
    # the produced body preserves the items-array shape the oven expects
    assert 'x.com.samsung.da.items' in body or 'x.com.samsung.da.temperature' in str(body)


def test_oven_setpoint_rmw_preserves_other_item_fields():
    """RMW must not drop sibling fields (e.g. x.com.samsung.da.current)."""
    desc = oven.OVEN_SETPOINT.entities[0]
    rep = {'x.com.samsung.da.items': [{
        'x.com.samsung.da.current': '180',
        'x.com.samsung.da.desired': '180',
    }]}
    path, body = desc.write_fn(200, rep)
    item = body['x.com.samsung.da.items'][0]
    assert item['x.com.samsung.da.desired'] == '200'
    # sibling field preserved
    assert item['x.com.samsung.da.current'] == '180'


def test_oven_setpoint_clamps_to_step():
    """Setpoint must be a multiple of SETPOINT_STEP_C."""
    desc = oven.OVEN_SETPOINT.entities[0]
    rep = {'x.com.samsung.da.items': [{'x.com.samsung.da.desired': '0'}]}
    _, body = desc.write_fn(202, rep)
    # 202 → nearest 5 = 200
    assert body['x.com.samsung.da.items'][0]['x.com.samsung.da.desired'] == '200'


def test_oven_setpoint_rejects_out_of_range():
    desc = oven.OVEN_SETPOINT.entities[0]
    rep = {'x.com.samsung.da.items': [{'x.com.samsung.da.desired': '100'}]}
    assert desc.write_fn(10, rep) is None    # below min (30)
    assert desc.write_fn(300, rep) is None   # above max (270)


def test_oven_setpoint_rejects_missing_items():
    desc = oven.OVEN_SETPOINT.entities[0]
    assert desc.write_fn(200, {}) is None


# ---------------------------------------------------------------------------
# OVEN_MODE — SelectDesc with non-empty options
# ---------------------------------------------------------------------------

def test_oven_mode_options_nonempty():
    assert len(oven.OVEN_MODE.entities[0].options) > 0


def test_oven_mode_write_round_trips():
    desc = oven.OVEN_MODE.entities[0]
    valid_mode = desc.options[1]   # e.g. 'Bake'
    path, body = desc.write_fn(valid_mode, {})
    assert path == ['mode', 'vs', '0']
    assert body['x.com.samsung.da.modes'] == [valid_mode]


def test_oven_mode_rejects_unknown():
    desc = oven.OVEN_MODE.entities[0]
    assert desc.write_fn('SpaghettiMode', {}) is None


# ---------------------------------------------------------------------------
# OVEN_MODE options-array RMW (lamp, sound, fast_preheat, natural_steam)
# ---------------------------------------------------------------------------

def _mode_rep(*extra_opts):
    return {'x.com.samsung.da.options': [
        'UpperLamp_Off', 'Sound_On', 'fastpreheat_Off', *extra_opts,
    ]}


def test_lamp_write_replaces_slot():
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == 'lamp')
    path, body = desc.write_fn('On', _mode_rep())
    opts = body['x.com.samsung.da.options']
    assert 'UpperLamp_On' in opts
    assert 'UpperLamp_Off' not in opts


def test_lamp_write_requires_existing_options():
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == 'lamp')
    assert desc.write_fn('On', {}) is None


def test_sound_write_preserves_other_options():
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == 'sound')
    path, body = desc.write_fn('Off', _mode_rep())
    opts = body['x.com.samsung.da.options']
    assert 'Sound_Off' in opts
    assert 'UpperLamp_Off' in opts     # other slot unchanged


def test_natural_steam_appended_if_absent():
    """NaturalSteam slot is absent until first write — write_fn must append it."""
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == 'natural_steam')
    path, body = desc.write_fn('On', _mode_rep())   # no NaturalSteam_* in rep
    opts = body['x.com.samsung.da.options']
    assert any(o.startswith('NaturalSteam_') for o in opts)


# ---------------------------------------------------------------------------
# OVEN_OPERATIONAL_STATE — cycle_active BinarySensorDesc
# ---------------------------------------------------------------------------

def test_cycle_active_true_when_running():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities
                if e.key == 'cycle_active')
    assert desc.value_fn('Run') is True
    assert desc.value_fn('Running') is True


def test_cycle_active_false_when_idle():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities
                if e.key == 'cycle_active')
    assert desc.value_fn('Ready') is False
    assert desc.value_fn(None) is False


# ---------------------------------------------------------------------------
# OVEN_OPERATIONAL_STATE — cook_time NumberDesc
# ---------------------------------------------------------------------------

def test_cook_time_write_produces_hms():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities
                if e.key == 'cook_time')
    path, body = desc.write_fn(90, {})
    assert path == ['operational', 'state', 'vs', '0']
    assert body['x.com.samsung.da.operationTime'] == '01:30:00'
    assert body['x.com.samsung.da.remainingTime'] == '01:30:00'


def test_cook_time_rejects_out_of_range():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities
                if e.key == 'cook_time')
    assert desc.write_fn(-1, {}) is None
    assert desc.write_fn(1440, {}) is None


# ---------------------------------------------------------------------------
# Adapter cycle_active_field wiring
# ---------------------------------------------------------------------------

def test_adapter_sets_cycle_active_field_for_oven():
    """build_runtime_descriptor must set cycle_active_field when oven keys present."""
    reg = {c.href: [c] for c in (oven.OVEN_OPERATIONAL_STATE, oven.OVEN_SETPOINT)}
    resources = {
        '/operational/state/vs/0': {'x.com.samsung.da.state': 'Run'},
        '/temperatures/vs/0': {'x.com.samsung.da.items': [
            {'x.com.samsung.da.current': '180', 'x.com.samsung.da.desired': '200'}
        ]},
    }
    bound = discover(resources, reg)
    rd = build_runtime_descriptor(
        bound, topic_prefix='t', ha_prefix='homeassistant',
        device_name='Oven', model='M', name='oven', default_port=49154)
    assert rd.cycle_active_field == 'cycle_active'
    flat = rd.flatten(resources)
    assert flat['cycle_active'] is True     # 'Run' -> active


def test_adapter_no_cycle_active_without_oven_keys():
    """Non-oven appliances must not get cycle_active_field."""
    from samsung_appliance.registry.capabilities import common
    reg = {c.href: [c] for c in (common.KIDS_LOCK,)}
    resources = {'/kidslock/vs/0': {'x.com.samsung.da.kidsLock': 'Ready'}}
    bound = discover(resources, reg)
    rd = build_runtime_descriptor(
        bound, topic_prefix='t', ha_prefix='homeassistant',
        device_name='Dishwasher', model='M', name='dishwasher', default_port=49154)
    assert rd.cycle_active_field is None

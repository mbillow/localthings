"""Unit tests for oven-family capabilities."""
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover


# ---------------------------------------------------------------------------
# Device-type detection + full-dump coverage (issue #55)
# ---------------------------------------------------------------------------

def test_oven_fixture_resolves_and_has_no_unbound_hrefs():
    """The issue #55 dump previously came back device_type='unknown' with
    /connected/vs/0 unbound -- resolving via the '-OVEN-' modelNum token
    fallback must leave every href in the oven registry bound or ignored."""
    from tests.conftest import _load_device
    resources = _load_device('oven')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'])
    assert reg is not None
    assert reg.name == 'oven'

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


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
# OVEN_SETPOINT — Fahrenheit bounds (issue #44 range dump reports
# unit="Fahrenheit"; bounds/step must track the live unit, not stay pinned
# to the Celsius defaults)
# ---------------------------------------------------------------------------

def _fahrenheit_rep(desired='0'):
    return {'x.com.samsung.da.items': [{
        'x.com.samsung.da.desired': desired,
        'x.com.samsung.da.unit': 'Fahrenheit',
    }]}


def test_oven_setpoint_write_uses_fahrenheit_bounds():
    desc = oven.OVEN_SETPOINT.entities[0]
    rep = _fahrenheit_rep()
    # 350 is within F bounds (175-550) but above the C max (270) --
    # confirms the write path isn't silently still clamping to Celsius.
    _, body = desc.write_fn(350, rep)
    assert body['x.com.samsung.da.items'][0]['x.com.samsung.da.desired'] == '350'


def test_oven_setpoint_rejects_out_of_range_fahrenheit():
    desc = oven.OVEN_SETPOINT.entities[0]
    rep = _fahrenheit_rep()
    assert desc.write_fn(100, rep) is None    # below F min (175)
    assert desc.write_fn(600, rep) is None    # above F max (550)


def test_oven_setpoint_native_bounds_track_live_unit():
    desc = oven.OVEN_SETPOINT.entities[0]
    celsius_rep = {'x.com.samsung.da.items': [{'x.com.samsung.da.unit': 'Celsius'}]}
    assert desc.native_min_fn(celsius_rep) == 30.0
    assert desc.native_max_fn(celsius_rep) == 270.0
    fahrenheit_rep = _fahrenheit_rep()
    assert desc.native_min_fn(fahrenheit_rep) == 175.0
    assert desc.native_max_fn(fahrenheit_rep) == 550.0
    assert desc.step_fn(fahrenheit_rep) == 5.0


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


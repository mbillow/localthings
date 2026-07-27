"""Tests for range/cooktop-oven combo support (issue #44)."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import range as range_caps
from custom_components.localthings.registry.discovery import discover

from tests.conftest import _load_device


def _range():
    resources = _load_device('range')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _state():
    reg, resources = _range()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_range_model_resolves_to_range_registry():
    reg, _ = _range()
    assert reg is not None and reg.name == 'range'


def test_no_unbound_hrefs():
    """Every resource in the issue #44 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _range()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        'power_switch', 'oven_setpoint', 'current_temp_c', 'oven_mode',
        'machine_state', 'door_open', 'cloud_connected', 'cooktop_state',
        'cooktop_safety_shutoff_enabled',
        'burner_0_power_level', 'burner_0_state', 'burner_0_hot_surface',
        'burner_3_power_level', 'burner_3_state', 'burner_3_hot_surface',
    ):
        assert key in state, key


def test_unreported_burners_gated_out():
    """Dump reports numberOfBurners=4 (indices 0-3) -- burner slots 4/5 of
    MAX_BURNERS must not appear as entities."""
    state = _state()
    for key in ('burner_4_power_level', 'burner_5_power_level'):
        assert key not in state, key


def test_burner_power_level_write_is_read_modify_write():
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities
                if e.key == 'burner_1_power_level')
    rep = {'burnerList': [
        {'burnerNumber': 0, 'powerLevel': '3'},
        {'burnerNumber': 1, 'powerLevel': '0'},
    ]}
    path, body = desc.write_fn('boost', rep)
    assert path == ['cooktop', 'status', 'vs', '0']
    burners = {b['burnerNumber']: b['powerLevel'] for b in body['burnerList']}
    assert burners[1] == 'boost'
    assert burners[0] == '3'   # sibling burner untouched


def test_burner_power_level_write_rejects_missing_burner():
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities
                if e.key == 'burner_2_power_level')
    rep = {'burnerList': [{'burnerNumber': 0, 'powerLevel': '0'}]}
    assert desc.write_fn('5', rep) is None


def test_burner_hot_surface_true_when_not_normal():
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities
                if e.key == 'burner_0_hot_surface')
    assert desc.value_fn([{'burnerNumber': 0, 'hotSurfaceState': 'hot'}]) is True
    assert desc.value_fn([{'burnerNumber': 0, 'hotSurfaceState': 'normal'}]) is False


def test_burner_pan_detected(): # issue #86
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities
                if e.key == 'burner_0_pan_detected')
    assert desc.value_fn([{'burnerNumber': 0, 'panDetection': True}]) is True
    assert desc.value_fn([{'burnerNumber': 0, 'panDetection': False}]) is False
    assert desc.value_fn([{'burnerNumber': 1, 'panDetection': True}]) is False  # different burner


def test_cooktop_power_is_read_only(): # issue #86
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities if e.key == 'cooktop_power')
    assert desc.value_fn('On') is True
    assert desc.value_fn('Off') is False
    assert not hasattr(desc, 'write_fn')  # BinarySensorDesc has no write path at all


def test_cooktop_child_lock_write(): # issue #86
    desc = next(e for e in range_caps.COOKTOP_STATUS.entities if e.key == 'cooktop_child_lock')
    assert desc.value_fn('on') is True
    assert desc.value_fn('off') is False
    path, body = desc.write_fn('On', {})
    assert path == ['cooktop', 'status', 'vs', '0']
    assert body == {'childLock': 'on'}
    assert desc.write_fn('Bogus', {}) is None


def test_probe_status_temperature_unit_reads_live_field(): # issue #86
    desc = next(e for e in range_caps.PROBE_STATUS.entities if e.key == 'probe_temperature')
    assert desc.unit_fn({'temperatureUnit': 'C'}) == '°C'
    assert desc.unit_fn({'temperatureUnit': 'F'}) == '°F'
    assert desc.unit_fn({}) == '°C'


def test_probe_status_connected_value_fn(): # issue #86
    desc = next(e for e in range_caps.PROBE_STATUS.entities if e.key == 'probe_connected')
    assert desc.value_fn('Connected') is True
    assert desc.value_fn('Disconnected') is False

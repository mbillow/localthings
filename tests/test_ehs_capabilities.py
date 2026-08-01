"""Tests for Samsung EHS (Eco Heating System) heat pump support
(TP1X_DA_AC_EHS_01001_0000).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the zone/dhw mode and temperature write contracts.
"""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc

from tests.conftest import _load_device


def _ehs():
    resources = _load_device('ehs')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound():
    reg, resources = _ehs()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_ehs_registry():
    reg, _ = _ehs()
    assert reg is not None and reg.name == 'ehs'


def test_no_unbound_hrefs():
    """Every resource in the real TP1X_DA_AC_EHS_01001_0000 dump binds or is
    covered -- clears the coverage-gap repair a device_type='unknown' entry
    raises."""
    reg, resources = _ehs()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in ('zone_power', 'zone_mode', 'zone_temperature', 'zone_target_temperature',
                'dhw_power', 'dhw_mode', 'dhw_temperature', 'dhw_target_temperature',
                'away_mode', 'mute_once', 'alarm_code', 'energy_kwh'):
        assert key in state, key


def test_zone_temperature_reads_current_value():
    state = _state()
    assert state['zone_temperature'] == 30.0


def test_zone_target_temperature_reads_desired_value():
    state = _state()
    assert state['zone_target_temperature'] == 5.0


def test_zone_mode_reads_first_mode():
    state = _state()
    assert state['zone_mode'] == 'Cool'


def test_zone_mode_select_options_come_from_live_supported_modes():
    """Options are read live from x.com.samsung.da.supportedModes, not a
    hardcoded tuple -- so a future firmware with a different mode set is
    handled automatically."""
    desc = _desc('zone_mode')
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == 'x.com.samsung.da.supportedModes'
    assert desc.options == ()


def test_zone_mode_write_contract():
    desc = _desc('zone_mode')
    path, body = desc.write_fn('Heat', {})
    assert path == ['mode', 'vs', '0']
    assert body == {'x.com.samsung.da.modes': ['Heat']}


def test_zone_power_reads_off():
    state = _state()
    assert state['zone_power'] is False


def test_zone_power_write_contract():
    desc = _desc('zone_power')
    path, body = desc.write_fn('On', {})
    assert path == ['power', 'vs', '0']
    assert body == {'x.com.samsung.da.power': 'On'}


def test_zone_target_temperature_write_contract():
    desc = _desc('zone_target_temperature')
    assert isinstance(desc, NumberDesc)
    path, body = desc.write_fn('21.5', {})
    assert path == ['temperatures', 'indoor', 'vs', '0']
    assert body == {'x.com.samsung.da.desired': '21.5'}


def test_zone_target_temperature_bounds_read_live():
    """min/max/step come from the device's own resource fields rather than
    a hardcoded constant -- see the adding-device-support skill's 'never
    hard-code the one dump's values' section."""
    desc = _desc('zone_target_temperature')
    assert desc.native_min_fn({'x.com.samsung.da.minimum': '5.0'}) == 5.0
    assert desc.native_max_fn({'x.com.samsung.da.maximum': '25.0'}) == 25.0
    assert desc.step_fn({'x.com.samsung.da.increment': '0.5'}) == 0.5
    # No live field: falls back to a sane default rather than raising.
    assert desc.native_min_fn({}) == 5.0
    assert desc.native_max_fn({}) == 30.0
    assert desc.step_fn({}) == 0.5


def test_dhw_temperature_reads_current_value():
    state = _state()
    assert state['dhw_temperature'] == 38.0


def test_dhw_target_temperature_reads_desired_value():
    state = _state()
    assert state['dhw_target_temperature'] == 40.0


def test_dhw_mode_reads_first_mode():
    state = _state()
    assert state['dhw_mode'] == 'Eco'


def test_dhw_mode_write_contract():
    desc = _desc('dhw_mode')
    path, body = desc.write_fn('Force', {})
    assert path == ['mode', 'dhw', 'vs', '0']
    assert body == {'x.com.samsung.da.modes': ['Force']}


def test_dhw_power_reads_on():
    state = _state()
    assert state['dhw_power'] is True


def test_dhw_power_write_contract():
    desc = _desc('dhw_power')
    path, body = desc.write_fn('Off', {})
    assert path == ['power', 'dhw', 'vs', '0']
    assert body == {'x.com.samsung.da.power': 'Off'}


def test_dhw_target_temperature_write_contract():
    desc = _desc('dhw_target_temperature')
    assert isinstance(desc, NumberDesc)
    path, body = desc.write_fn('45.0', {})
    assert path == ['temperatures', 'dhw', 'vs', '0']
    assert body == {'x.com.samsung.da.desired': '45.0'}


def test_away_mode_reads_off():
    state = _state()
    assert state['away_mode'] is False


def test_away_mode_write_contract():
    desc = _desc('away_mode')
    path, body = desc.write_fn('On', {})
    assert path == ['option', 'outgoing', 'vs', '0']
    assert body == {'x.com.samsung.da.away': 'On'}

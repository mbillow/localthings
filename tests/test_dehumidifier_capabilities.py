"""Tests for Samsung dehumidifier support (issue #88, AY18CG7500GED).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the target-humidity/operating-mode write contracts.
"""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc

from tests.conftest import _load_device


def _dehumidifier():
    resources = _load_device('dehumidifier')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound():
    reg, resources = _dehumidifier()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_dehumidifier_registry():
    reg, _ = _dehumidifier()
    assert reg is not None and reg.name == 'dehumidifier'


def test_no_unbound_hrefs():
    """Every resource in the issue #88 dump binds or is covered -- clears
    the coverage-gap repair."""
    reg, resources = _dehumidifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in ('humidity', 'target_humidity', 'operating_mode', 'power_switch',
                'auto_clean', 'air_filter_status', 'mute_once'):
        assert key in state, key


def test_humidity_sensor_reads_current_value():
    state = _state()
    assert state['humidity'] == 47


def test_target_humidity_number_reads_desired_value():
    state = _state()
    assert state['target_humidity'] == 50


def test_target_humidity_write_contract():
    desc = _desc('target_humidity')
    assert isinstance(desc, NumberDesc)
    path, body = desc.write_fn('55', {})
    assert path == ['humidity', 'vs', '0']
    assert body == {'x.com.samsung.da.desiredHumidity': '55'}


def test_target_humidity_step_reads_live_increment():
    """Step comes from the device's own `increment` field rather than a
    hardcoded constant -- see the adding-device-support skill's 'never
    hard-code the one dump's values' section."""
    desc = _desc('target_humidity')
    assert desc.step_fn({'increment': '5'}) == 5
    assert desc.step_fn({'increment': '10'}) == 10
    # No live field: falls back to a sane default rather than raising.
    assert desc.step_fn({}) == 1


def test_target_humidity_has_no_hardcoded_bounds():
    """No range field is present in any dump seen so far -- native_min/max
    are deliberately left unset (falls back to HA's own 0-100 default for a
    percentage field) rather than a bound guessed from a spec sheet."""
    desc = _desc('target_humidity')
    assert desc.native_min is None
    assert desc.native_max is None
    assert desc.native_min_fn is None
    assert desc.native_max_fn is None


def test_operating_mode_select_options_come_from_live_supported_modes():
    """Options are read live from x.com.samsung.da.supportedModes, not a
    hardcoded tuple -- so a future device with a different mode set is
    handled automatically."""
    desc = _desc('operating_mode')
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == 'x.com.samsung.da.supportedModes'
    assert desc.options == ()


def test_operating_mode_write_contract():
    desc = _desc('operating_mode')
    path, body = desc.write_fn('Quiet', {})
    assert path == ['mode', 'vs', '0']
    assert body == {'x.com.samsung.da.modes': ['Quiet']}


def test_operating_mode_reads_first_mode():
    state = _state()
    assert state['operating_mode'] == 'Smart'

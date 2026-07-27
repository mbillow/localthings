"""Tests for Samsung water-purifier support (issue #90, TP2X_WATERPURIFIER_20K).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the write contracts.
"""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc

from tests.conftest import _load_device


def _water_purifier():
    resources = _load_device('water_purifier')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound():
    reg, resources = _water_purifier()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_water_purifier_registry():
    reg, _ = _water_purifier()
    assert reg is not None and reg.name == 'water_purifier'


def test_no_unbound_hrefs():
    """Every resource in the issue #90 dump binds or is covered -- clears
    the coverage-gap repair."""
    reg, resources = _water_purifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in ('dispense_type', 'hot_water_temperature', 'dispense_capacity',
                'pouring', 'waterpurifier_status', 'filter_usage', 'filter_status',
                'hotwater_lock', 'coldwater_lock', 'buzz_lock'):
        assert key in state, key


def test_dispense_type_options_come_from_live_supported_types():
    desc = _desc('dispense_type')
    assert desc.options_field == 'x.com.samsung.da.supportedTypes'


def test_dispense_type_write_contract():
    desc = _desc('dispense_type')
    path, body = desc.write_fn('hotwater', {})
    assert path == ['setting', 'waterpurifier', 'vs', '0']
    assert body == {'x.com.samsung.da.desiredType': 'hotwater'}


def test_hot_water_temperature_is_a_select_not_a_number():
    """Only a handful of discrete temperatures are selectable (not a
    continuous range) -- confirmed by supportedHotTemperatures being a short
    enumerated list, not a [min, max] range field."""
    desc = _desc('hot_water_temperature')
    assert desc.options_field == 'x.com.samsung.da.supportedHotTemperatures'


def test_dispense_capacity_bounds_come_live_not_hardcoded():
    """Bounds and step come from the device's own desiredCapacityRange/
    capacityResolution fields, not a hardcoded constant -- see the
    adding-device-support skill's 'never hard-code the one dump's values'
    section."""
    desc = _desc('dispense_capacity')
    assert isinstance(desc, NumberDesc)
    assert desc.native_min is None
    assert desc.native_max is None
    assert desc.range_field == 'x.com.samsung.da.desiredCapacityRange'
    rep = {'x.com.samsung.da.desiredCapacityRange': ['50', '2000'],
           'x.com.samsung.da.capacityResolution': '10'}
    assert desc.step_fn(rep) == 10


def test_dispense_capacity_write_contract():
    desc = _desc('dispense_capacity')
    path, body = desc.write_fn('550', {})
    assert path == ['setting', 'waterpurifier', 'vs', '0']
    assert body == {'x.com.samsung.da.desiredCapacity': '550'}


def test_lock_switches_read_unlocked_as_off():
    state = _state()
    assert state['hotwater_lock'] is False
    assert state['coldwater_lock'] is False
    assert state['buzz_lock'] is False


def test_lock_switch_write_contracts():
    hot = _desc('hotwater_lock')
    cold = _desc('coldwater_lock')
    buzz = _desc('buzz_lock')
    assert hot.write_fn('On', {}) == (
        ['status', 'lock', 'vs', '0'], {'x.com.samsung.da.hotwaterLock': 'Locked'})
    assert cold.write_fn('Off', {}) == (
        ['status', 'lock', 'vs', '0'], {'x.com.samsung.da.coldwaterLock': 'Unlocked'})
    assert buzz.write_fn('On', {}) == (
        ['status', 'lock', 'vs', '0'], {'x.com.samsung.da.buzzLock': 'Locked'})


def test_favorite_capacity_options_come_from_live_capacity_list():
    desc = _desc('favorite_capacity')
    assert desc.options_field == 'x.com.samsung.da.capacityList'


def test_sterilize_timestamps_parsed_as_utc():
    state = _state()
    assert state['sterilize_last_time'].tzinfo is not None
    assert state['sterilize_plan_time'].tzinfo is not None


def test_mode_hrefs_are_ignored_not_guessed():
    """/mode/vs/0's supportedModes carries a single opaque wizard token and
    modes reports an unrelated value not even in supportedModes -- internal
    plumbing, left unmodeled per the 'don't guess' rule rather than exposed
    as a nonsensical select."""
    from custom_components.localthings.registry.capabilities import water_purifier
    ignored_hrefs = {cap.href for cap in water_purifier.COVERAGE}
    assert '/mode/vs/0' in ignored_hrefs
    assert '/automation/waterpurifier/vs/0' in ignored_hrefs


# ---------------------------------------------------------------------------
# Coffee-capable variant (issue #107) -- /favorite/coffee/vs/0 and
# /favorite/hotwater/vs/0, not present in issue #90's original dump.
# ---------------------------------------------------------------------------

def _water_purifier_coffee():
    resources = _load_device('water_purifier_coffee')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound_coffee():
    reg, resources = _water_purifier_coffee()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state_coffee():
    bound, resources = _bound_coffee()
    return flatten(bound, resources)


def _desc_coffee(key):
    bound, _ = _bound_coffee()
    return next(b.desc for b in bound if b.desc.key == key)


def test_coffee_variant_no_unbound_hrefs():
    """Every resource in the issue #107 dump binds or is covered, including
    the four coffee-recipe hrefs not present in issue #90's dump."""
    reg, resources = _water_purifier_coffee()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_coffee_variant_expected_state_keys_present():
    state = _state_coffee()
    for key in ('favorite_coffee_enabled', 'coffee_brew_status',
                'favorite_hotwater_enabled', 'favorite_hotwater_temperature'):
        assert key in state, key


def test_favorite_coffee_write_contract():
    desc = _desc_coffee('favorite_coffee_enabled')
    assert desc.write_fn('On', {}) == (
        ['favorite', 'coffee', 'vs', '0'], {'favorite.activate': 'On'})
    assert desc.write_fn('Off', {}) == (
        ['favorite', 'coffee', 'vs', '0'], {'favorite.activate': 'Off'})


def test_favorite_hotwater_write_contract():
    enabled = _desc_coffee('favorite_hotwater_enabled')
    assert enabled.write_fn('On', {}) == (
        ['favorite', 'hotwater', 'vs', '0'], {'x.com.samsung.da.switchHotwater': 'Unlocked'})
    assert enabled.write_fn('Off', {}) == (
        ['favorite', 'hotwater', 'vs', '0'], {'x.com.samsung.da.switchHotwater': 'Locked'})


def test_favorite_hotwater_temperature_options_come_from_live_supported_list():
    desc = _desc_coffee('favorite_hotwater_temperature')
    assert desc.options_field == 'x.com.samsung.da.favorite.supportedList'


def test_coffee_recipe_hrefs_are_ignored_not_guessed():
    """Static capability-advertisement blobs or empty resources -- no live
    'current recipe'/'current custom slot' field to expose, per the 'don't
    guess' rule."""
    from custom_components.localthings.registry.capabilities import water_purifier
    ignored_hrefs = {cap.href for cap in water_purifier.COVERAGE}
    for href in ('/brand/recipe/info/vs/0', '/coffee/custom/recipe/vs/0',
                 '/recipe/coffee/vs/0', '/recipe/coffee/deletion/vs/0'):
        assert href in ignored_hrefs, href

"""Tests for Samsung air-conditioner support (issue #17).

These stay HA-free like the rest of the suite: they exercise the registry,
discovery/flatten, and the CLIMATE capability's write contract. The composite
climate entity itself lives in climate.py (imports homeassistant) and is not
importable here -- consistent with how the other HA platform files are untested.
"""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device, for_device_by_model
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc

from tests.conftest import _load_device


def _ac():
    resources = _load_device('airconditioner')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound():
    reg, resources = _ac()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def test_ac_model_resolves_to_airconditioner_registry():
    reg, _ = _ac()
    assert reg is not None and reg.name == 'airconditioner'


def test_no_unbound_hrefs():
    """Every resource in the issue #17 dump binds or is covered -- clears the
    coverage-gap repair."""
    reg, resources = _ac()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_climate_entity_is_bound():
    """The composite climate entity binds the primary /mode/vs/0 resource."""
    bound, _ = _bound()
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1
    assert climate[0].href == '/mode/vs/0'


def test_expected_state_keys_present():
    state = _state()
    for key in ('climate', 'air_purify', 'auto_clean', 'air_filter_status',
                'air_filter_usage', 'diagnosis_status', 'alarm_code', 'energy_kwh'):
        assert key in state, key


def test_power_and_convenient_folded_into_climate():
    """On/off is the climate entity's HVACMode.OFF and convenient mode is its
    preset_mode -- neither surfaces as a standalone switch/select."""
    state = _state()
    assert 'power_switch' not in state
    assert 'convenient_mode' not in state


def test_air_filter_usage_is_percentage_of_capacity():
    """filterUsage is a raw count in the capacity unit (100 of 500), surfaced as
    a percentage rather than the misleading raw value."""
    assert _state()['air_filter_usage'] == 20


def test_climate_write_targets():
    """The CLIMATE write_fn maps each (kind, value) command to the right OCF
    POST target and body. `value` is already the raw device code."""
    write = airconditioner.CLIMATE.entities[0].write_fn
    assert write(('power', True), {}) == (['power', '0'], {'value': True})
    assert write(('power', False), {}) == (['power', '0'], {'value': False})
    assert write(('mode', 'Heat'), {}) == (
        ['mode', 'vs', '0'], {'x.com.samsung.da.modes': ['Heat']})
    assert write(('temperature', 23.6), {}) == (
        ['temperature', 'desired', '0'], {'temperature': 24})
    assert write(('fan', '2'), {}) == (
        ['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': '2'})
    assert write(('swing', 'All'), {}) == (
        ['wind', 'direction', 'vs', '0'], {'x.com.samsung.da.modes': 'All'})
    assert write(('preset', 'Sleep'), {}) == (
        ['mode', 'convenient', 'vs', '0'], {'x.com.samsung.da.modes': 'Sleep'})
    assert write(('bogus', 1), {}) is None


def test_climate_consumed_hrefs_declared_as_coverage():
    """The climate-consumed and ambiguous hrefs are declared in the AC registry
    (as no-entity coverage caps) so they don't leak as gaps -- but produce no
    standalone entities."""
    reg, _ = _ac()
    for href in ('/power/0', '/power/vs/0', '/temperature/desired/0',
                 '/wind/strength/vs/0', '/mode/convenient/vs/0',
                 '/temperatures/vs/0', '/sensors/vs/0', '/humidity/0'):
        caps = reg.capabilities.get(href)
        assert caps, href
        assert all(c.entities == () for c in caps), href


# ---------------------------------------------------------------------------
# TP1X_DA-AC-RAC-01011 (oneUiVersion "7.0 Air conditioner", Tizen Lite) -- a
# newer model class than the ARTIK051_PRAC dump above. It has no OCF-standard
# /temperature/current+desired pair (temperature lives on the vendor
# /temperatures/vs/0 items[] resource), exposes a /light/vs/0 display light, and
# carries 13 extra vendor housekeeping hrefs. Issue #17 for this class.
# ---------------------------------------------------------------------------

def _ac_tp1x():
    resources = _load_device('airconditioner_tp1x_da_ac_rac_01011')
    one_ui = resources['/otninformation/vs/0']['swVersionInfo']['oneUiVersion']
    return for_device(one_ui), resources


def test_tp1x_resolves_to_airconditioner_registry():
    reg, _ = _ac_tp1x()
    assert reg is not None and reg.name == 'airconditioner'


def test_tp1x_no_unbound_hrefs():
    """Every resource in the TP1X dump binds or is covered -- including
    /temperatures/vs/0, /light/vs/0 and the 13 housekeeping hrefs absent from
    the ARTIK051 dump. Clears the coverage-gap repair."""
    reg, resources = _ac_tp1x()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_tp1x_display_light_switch_present():
    """/light/vs/0 (mode On/Off) surfaces as the display-light switch."""
    reg, resources = _ac_tp1x()
    state = flatten(
        discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state.get('display_light') is True  # device reports mode == 'On'


def test_tp1x_vendor_temperature_and_light_covered():
    """The vendor temperature resource (read by the climate entity) and the
    display-light resource both resolve in the registry -- no gap."""
    reg, _ = _ac_tp1x()
    assert reg.capabilities.get('/temperatures/vs/0'), '/temperatures/vs/0'
    assert reg.capabilities.get('/light/vs/0'), '/light/vs/0'


def test_tp1x_climate_entity_is_bound():
    """The composite climate entity still binds the primary /mode/vs/0."""
    reg, resources = _ac_tp1x()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1 and climate[0].href == '/mode/vs/0'

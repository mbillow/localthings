"""Tests for the no-/information/vs/0, no-burner-status range variant
(NE63B8411SS-class, issue #74)."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_resources
from custom_components.localthings.registry.capabilities import range as range_caps
from custom_components.localthings.registry.discovery import discover

from tests.conftest import _load_device


def _range():
    resources = _load_device('range_no_info')
    reg = for_device_by_resources(resources)
    return reg, resources


def _state():
    reg, resources = _range()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_range_registry():
    reg, _ = _range()
    assert reg is not None and reg.name == 'range'


def test_no_unbound_hrefs():
    """Every resource in the issue #74 dump binds or is ignored -- clears
    the coverage-gap repair."""
    reg, resources = _range()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_no_burner_entities():
    """This board reports no /cooktop/status/vs/0, so none of range.py's
    per-burner entities should appear -- only COOKTOP_MONITORING's."""
    state = _state()
    assert not any(k.startswith('burner_') for k in state)


def test_expected_entities_present():
    state = _state()
    for key in (
        'power_switch', 'oven_setpoint', 'current_temp_c', 'oven_mode',
        'machine_state', 'door_open', 'cloud_connected',
        'cooktop_running_state', 'warming_center_state',
    ):
        assert key in state, key


def test_cooktop_monitoring_reads_live_fields():
    desc = next(e for e in range_caps.COOKTOP_MONITORING.entities if e.key == 'cooktop_running_state')
    assert desc.value_fn('Ready') == 'Ready'
    desc = next(e for e in range_caps.COOKTOP_MONITORING.entities if e.key == 'warming_center_state')
    assert desc.value_fn('Off') == 'Off'

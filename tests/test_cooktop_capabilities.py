"""Tests for the read-only cooktop capability profile."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import cooktop
from custom_components.localthings.registry.capabilities.cooktop import (
    COOKTOP_MODE,
)
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _state(resources):
    bound = discover(
        resources,
        cooktop.REGISTRY.capabilities,
        cooktop.REGISTRY.pattern_capabilities,
    )
    return flatten(bound, resources)


def test_real_cooktop_fixture_has_expected_idle_state():
    state = _state(_load_device('cooktop'))

    assert state['power_state'] is True
    assert state['any_burner_active'] is False
    assert state['burner_0_state'] == 'Ready'
    assert state['burner_5_state'] == 'Ready'
    assert state['main_timer_state'] == 'Ready'
    assert state['main_timer_current'] == 0
    assert state['cloud_connected'] is True
    assert state['paired_hood_connected'] is False
    assert state['paired_hood_power'] is False
    assert state['paired_hood_fan_speed'] == 0
    assert state['paired_hood_light'] is False


def test_run_operation_marks_any_burner_active():
    resources = _load_device('cooktop')
    options = resources['/mode/vs/0']['x.com.samsung.da.options']
    options[options.index('OperationState3_Ready')] = 'OperationState3_Run'

    state = _state(resources)

    assert state['burner_3_state'] == 'Run'
    assert state['any_burner_active'] is True


def test_cooktop_profile_has_no_write_functions():
    """A local integration must never expose unverified remote ignition."""
    descriptions = [
        desc
        for capabilities in cooktop.REGISTRY.capabilities.values()
        for capability in capabilities
        for desc in capability.entities
    ]

    assert descriptions
    assert all(not hasattr(desc, 'write_fn') or desc.write_fn is None
               for desc in descriptions)


def test_static_slot_superset_has_headroom_for_other_layouts():
    keys = {desc.key for desc in COOKTOP_MODE.entities}

    assert {f'burner_{slot}_state' for slot in range(8)} <= keys


def test_variant_slot_outside_na9300k_layout_is_discovered():
    resources = _load_device('cooktop')
    resources['/mode/vs/0']['x.com.samsung.da.options'].append(
        'OperationState7_Ready',
    )

    state = _state(resources)

    assert state['burner_7_state'] == 'Ready'
    assert 'burner_2_state' not in state

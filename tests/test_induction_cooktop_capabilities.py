"""Tests for the standalone induction-cooktop registry (issue #86)."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model, induction_cooktop
from custom_components.localthings.registry.discovery import discover

from tests.conftest import _load_device


def _cooktop():
    resources = _load_device('induction_cooktop')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _state():
    reg, resources = _cooktop()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_model_resolves_to_induction_cooktop_registry():
    reg, _ = _cooktop()
    assert reg is not None and reg.name == 'induction_cooktop'
    assert reg is induction_cooktop.REGISTRY


def test_no_unbound_hrefs():
    """Every resource in the issue #86 dump binds or is ignored -- clears
    the coverage-gap repair."""
    reg, resources = _cooktop()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        'cooktop_state', 'cooktop_power', 'cooktop_child_lock',
        'burner_0_power_level', 'burner_0_state', 'burner_0_hot_surface',
        'burner_0_pan_detected', 'burner_2_pan_detected',
        'paired_hood_connected', 'probe_connected', 'probe_temperature',
        'energy_kwh', 'alarm_code',
    ):
        assert key in state, key


def test_unreported_burners_gated_out():
    """Dump reports numberOfBurners=3 (indices 0-2) -- burner slot 3+ of
    range.MAX_BURNERS must not appear as entities."""
    state = _state()
    assert 'burner_3_power_level' not in state


def test_recipe_status_href_is_ignored_not_unbound():
    """/cooktop/recipe/status/vs/0 is idle/empty on this dump -- covered by
    ignored.py, not modeled as an entity (same treatment as the microwave
    family's /recipe/cook/vs/0)."""
    reg, _ = _cooktop()
    ignored_hrefs = {cap.href for caps in reg.capabilities.values() for cap in caps
                     if cap.entities == ()}
    assert '/cooktop/recipe/status/vs/0' in ignored_hrefs

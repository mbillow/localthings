"""Tests for the TP2X_DA-KS-COOKTOP-000001 NV9300K induction cooktop (issue #508).

Reported as a gas cooktop because the registry this burner-options surface
routes to was named 'gas_cooktop'; the surface itself says nothing about
fuel. Its per-zone PowerLevel/HotSurface options were read by nothing.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.identity import device_display_name
from tests.conftest import _load_device


def _state(name):
    resources = _load_device(name)
    reg = resolve(resources)
    assert reg is not None
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


def test_resolves_to_fuel_neutral_cooktop_registry():
    reg = resolve(_load_device("cooktop_nv9300k"))
    assert reg is not None and reg.name == "cooktop"
    assert device_display_name(reg.name, "") == "Samsung Cooktop"


def test_no_unbound_hrefs():
    resources = _load_device("cooktop_nv9300k")
    reg = resolve(resources)
    assert reg is not None
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_per_zone_power_level_and_hot_surface():
    state = _state("cooktop_nv9300k")
    # Slot 2 is not advertised on this board, so it gets no entities.
    for slot in (0, 1, 3, 4, 5):
        assert state[f"burner_{slot}_power_level"] == "Off"
        assert state[f"burner_{slot}_hot_surface"] is False
    assert "burner_2_power_level" not in state
    assert "burner_2_hot_surface" not in state


def test_gas_cooktop_gets_no_induction_sensors():
    state = _state("cooktop")
    assert not [k for k in state if k.endswith(("_power_level", "_hot_surface"))]

"""Read-only capability coverage for the TP6X_CT_16K legacy cooktop.

The captured options omit slot 2 and report pan sizes only for slots 3 and 5.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.identity import device_display_name
from tests.conftest import _load_device

NAME = "cooktop_tp6x_ct_16k_8888"


def _state():
    resources = _load_device(NAME)
    reg = resolve(resources)
    assert reg is not None
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


def test_resolves_to_cooktop_registry():
    reg = resolve(_load_device(NAME))
    assert reg is not None and reg.name == "cooktop"
    assert device_display_name(reg.name, "") == "Samsung Cooktop"


def test_no_unbound_hrefs():
    resources = _load_device(NAME)
    reg = resolve(resources)
    assert reg is not None
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_per_zone_power_level_hot_surface_and_state():
    state = _state()
    for slot in (0, 1, 3, 4, 5):
        assert state[f"burner_{slot}_state"] == "Ready"
        assert state[f"burner_{slot}_power_level"] == "Off"
        assert state[f"burner_{slot}_hot_surface"] is False
    assert "burner_2_state" not in state
    assert "burner_2_power_level" not in state
    assert "burner_2_hot_surface" not in state


def test_only_slots_3_and_5_report_pan_size():
    state = _state()
    assert state["burner_3_pan_size"] == "Idle"
    assert state["burner_5_pan_size"] == "Single"
    assert "burner_0_pan_size" not in state
    assert "burner_1_pan_size" not in state
    assert "burner_4_pan_size" not in state


def test_legacy_mode_options_surface():
    state = _state()
    assert state["cooktop_model"] == "NV9300K-/AA2"
    assert state["cooktop_paused"] is False
    assert state["cooktop_sync_flex"] is False
    assert state["cooktop_flex_coil"] == "0"
    assert state["main_timer_current"] == 0
    assert state["main_timer_set"] == 0
    assert state["main_timer_state"] == "Ready"


def test_housekeeping_sensors():
    state = _state()
    assert state["any_burner_active"] is False
    assert state["alarm_code"] == "none"
    assert state["child_lock"] is False
    assert state["diagnosis_status"] == "ready"
    assert state["power_state"] is False
    assert state["remote_control"] is True

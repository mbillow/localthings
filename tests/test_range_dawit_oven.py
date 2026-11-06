"""The DAWIT 3.0 range's oven half (issue #726, OS70H30E/AA0).

Its oven reports only the bare-field /oven/status/vs/0 and
/oven/settings/status/vs/0 the ME80H microwave uses, so before this it had
cooktop entities and no oven ones at all.
"""

from __future__ import annotations

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_OIC = ("oic.wk.d", "oic.d.range")


def _state(resources):
    registry = resolve(resources, device_types=_OIC)
    assert registry is not None and registry.name == "range"
    unbound: list[str] = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    return flatten(bound, resources), unbound


def test_every_resource_binds():
    _, unbound = _state(_load_device("range_os70h30e"))
    assert unbound == []


def test_oven_status_reads():
    state, _ = _state(_load_device("range_os70h30e"))
    assert state["machine_state"] == "idle"
    assert state["door_open"] is False
    # At rest: target 0 is unset and 175 F is Bake's floor, as on the other
    # ovens.
    assert state["current_temp_c"] is None
    assert state["oven_setpoint"] is None
    assert state["lamp"] is False
    assert state["remote_control"] is True
    assert state["energy_saving"] is True


def test_a_running_bake_reads_as_active():
    resources = _load_device("range_os70h30e")
    status = {
        **resources["/oven/status/vs/0"],
        "operation": "running",
        "lamp": "on",
        "door": {"state": "open", "lock": "off"},
        "mode": {"name": "Bake"},
        "temperature": {"unit": "fahrenheit", "target": 350, "current": 210},
        "time": {"operation": "running", "setting": 3600, "completion": "", "remaining": 3300},
    }
    state, _ = _state({**resources, "/oven/status/vs/0": status})
    assert state["machine_state"] == "active"
    assert state["cycle_active"] is True
    assert state["cooking_mode"] == "Bake"
    assert state["oven_setpoint"] == 350
    assert state["current_temp_c"] == 210.0
    assert state["cook_time_remaining"] == 3300
    assert state["lamp"] is True
    assert state["door_open"] is True


def test_not_bound_beside_an_ocf_cavity():
    resources = {
        **_load_device("range_os70h30e"),
        "/oven/vs/0": {"x.com.samsung.da.state": "Ready"},
    }
    registry = resolve(resources, device_types=_OIC)
    assert registry is not None
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    assert not any(b.href in ("/oven/status/vs/0", "/oven/settings/status/vs/0") for b in bound)

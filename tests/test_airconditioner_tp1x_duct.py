"""Tests for the TP1X_DA-AC-DUCT slim duct air conditioner (issue #501).

Routes on /oic/d's oic.d.airconditioner; the two gaps were the
auto-changeover settings and the dual (cooling/heating) setpoint resource.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc
from tests.conftest import _load_device

_DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _airconditioner():
    resources = _load_device("airconditioner_tp1x_duct")
    return resolve(resources, device_types=_DEVICE_TYPES), resources


def _bound():
    reg, resources = _airconditioner()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def test_resolves_to_airconditioner_registry():
    reg, _ = _airconditioner()
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    reg, resources = _airconditioner()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_auto_changeover_switch_reads_on_and_has_write_contract():
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["auto_changeover"] is True
    desc = next(b.desc for b in bound if b.desc.key == "auto_changeover")
    assert isinstance(desc, SwitchDesc)
    assert desc.write_fn("Off", {}) == (["autochangeover", "vs", "0"], {"status": "Off"})


def test_auto_changeover_offsets_are_numeric():
    state = _state()
    assert state["auto_changeover_cool_primary_offset"] == 2.0
    assert state["auto_changeover_heat_secondary_offset"] == 4.0


def test_dual_setpoint_reads_status_and_setpoints_in_device_unit():
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["dual_setpoint_enabled"] is False
    assert state["dual_setpoint_cooling"] == 75.0
    assert state["dual_setpoint_heating"] == 75.0
    desc = next(b.desc for b in bound if b.desc.key == "dual_setpoint_heating")
    assert desc.unit_fn(resources["/temperatures/dualsetpoint/vs/0"]) == "°F"

"""25K_REF_LCD_FHUB10.0 Family Hub side-by-side fridge (RS6HA8880S9/EG, Tizen 7).

Read per-href over OwnerPSK: its /device/0 Collection answers with links only
and silently drops if=oic.if.b. Locks in full registry coverage (zero unbound
hrefs) and key live readings from the redacted diagnostics capture.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

FIXTURE = "refrigerator_25k_ref_lcd_fhub"


def _fridge():
    resources = _load_device(FIXTURE)
    return resolve(resources, device_types=("oic.wk.d", "oic.d.refrigerator")), resources


def _state():
    reg, resources = _fridge()
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_refrigerator_registry():
    reg, _ = _fridge()
    assert reg is not None and reg.name == "refrigerator"


def test_resolves_to_refrigerator_registry_by_board_token_alone():
    resources = _load_device(FIXTURE)
    reg = resolve(resources)
    assert reg is not None and reg.name == "refrigerator"


def test_no_unbound_hrefs():
    reg, resources = _fridge()
    assert reg is not None
    unbound: list[str] = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_key_fridge_readings():
    state = _state()
    assert state["cooler_temperature"] == 3.0
    assert state["cooler_setpoint"] == 3.0
    assert state["freezer_temperature"] == -19.0
    assert state["freezer_setpoint"] == -19.0
    assert state["door_cooler_open"] is True
    assert state["door_freezer_open"] is False
    assert state["rapid_fridge"] is False
    assert state["rapid_freezing"] is False
    assert state["vacation_mode"] is False
    assert state["icemaker_one_enabled"] is True
    assert state["icemaker_one_making_status"] == "icestatus_stop"
    assert state["filter_usage"] == "100"
    assert state["filter_status"] == "replace"
    assert state["ai_energy_level"] == "1"
    assert state["energy_this_month_kwh"] == 11.06

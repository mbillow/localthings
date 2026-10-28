"""Issue #682: the MIM-H04EN EHS Wi-Fi kit and the SAC_EHS_MONO heat pump
behind it, from the reporter's 0.35.0 diagnostics."""

import json

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SensorDesc
from tests.conftest import FIXTURES, _discover_full, _load_device_full

_NAME = "ehs_mim_h04en"
_DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")
_HEAT_PUMP = "/0a1b2c3d-0000-4000-8000-000001200000/device/0"


def _registry(resources, device_types=()):
    reg = resolve(resources, device_types=device_types)
    assert reg is not None
    return reg


def _unbound(resources, device_types=()):
    reg = _registry(resources, device_types)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return reg.name, unbound


def _heat_pump():
    _, _, seeds = _load_device_full(_NAME)
    return parse_device0_batch(seeds[_HEAT_PUMP])


def _heat_pump_state():
    resources = _heat_pump()
    reg = _registry(resources)
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


def test_the_collections_own_batch_entry_is_not_a_resource():
    raw = json.loads((FIXTURES / f"{_NAME}_device.json").read_text())
    assert raw["device0"][0]["href"] == "/device/0"
    resources, _, _ = _load_device_full(_NAME)
    assert "/device/0" not in resources
    assert "/device/0" not in _heat_pump()


def test_the_wifi_kit_is_fully_covered():
    resources, _, _ = _load_device_full(_NAME)
    assert _unbound(resources, _DEVICE_TYPES) == ("airconditioner", [])


def test_the_heat_pump_is_fully_covered():
    assert _unbound(_heat_pump()) == ("ehs", [])


def test_the_heat_pump_materializes_behind_the_kit():
    resources, oic_res, seeds = _load_device_full(_NAME)
    _, materialized, skipped, _, name = _discover_full(resources, oic_res, seeds, _DEVICE_TYPES)
    assert name == "airconditioner"
    assert [s.key for s in materialized] == ["0a1b2c3d-0000-4000-8000-000001200000"]
    assert skipped == []


def test_the_heat_pumps_new_readings():
    state = _heat_pump_state()
    assert state["outdoor_temperature"] == 7.9
    assert state["heat_generated_kwh"] == 23294.29
    assert state["booster_heater"] is False
    assert state["three_way_valve"] == "Room"


def test_power_in_kw_is_labelled_kw():
    resources = _heat_pump()
    reg = _registry(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    desc = next(b.desc for b in bound if b.desc.key == "power_watts")
    assert isinstance(desc, SensorDesc) and desc.unit_fn is not None
    rep = resources["/energy/consumption/vs/0"]
    assert flatten(bound, resources)["power_watts"] == 1.034
    assert desc.unit_fn(rep) == "kW"
    assert desc.unit_fn({**rep, "x.com.samsung.da.instantaneousPowerUnit": "W"}) == "W"
    assert desc.unit_fn({}) == "W"

"""Captured unpaired bridge and explicitly constructed paired-hood cases."""

import json
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http import table_for, to_resources
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_resources, resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SensorDesc
from custom_components.localthings.registry.subdevices import _has_live_primary_entity
from tests.conftest import _load_device

_OPTIONS = "x.com.samsung.da.options"
_POWER = "x.com.samsung.da.power"
_UNPAIRED = "range_hood_tp6x_ct_unpaired"


def _hood_resources(model="SYNTHETIC_HOOD"):
    """Build protocol-shaped input solely to exercise the proposed mappings."""
    return {
        "/mode/vs/0": {
            _OPTIONS: [
                f"DeviceType_{model}",
                "BTState_Connected",
                "Bluetooth_On",
                "FanSpeed_2",
                "Lamp_On",
                "ShutOffTimerSet_10",
                "ShutOffTimerCurrent_5",
                "ShutOffTimerState_Run",
            ]
        },
        "/power/vs/0": {_POWER: "Off"},
        "/connected/vs/0": {"x.com.samsung.da.connected": "On"},
        "/remotectrl/vs/0": {"x.com.samsung.da.remoteControlEnabled": True},
        "/diagnosis/vs/0": {"x.com.samsung.da.diagnosisStart": "Ready"},
    }


def _bind(resources):
    registry = resolve(resources)
    assert registry is not None
    unbound = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    assert unbound == []
    return bound, flatten(bound, resources)


def test_parent_family_translates_captured_bridge_envelopes():
    dump = json.loads((Path(__file__).parent / "fixtures" / f"{_UNPAIRED}_device.json").read_text())
    parent = _load_device("cooktop_tp6x_ct_16k_8888")
    # Indexed siblings share the envelope table selected from the parent appliance.
    family = parent["/information/vs/0"]["x.com.samsung.da.description"]
    translated = to_resources(dump["legacy_bodies"], table_for(family))
    expected = _load_device(_UNPAIRED)
    expected.pop("/connected/vs/0")
    assert translated == expected


@pytest.mark.parametrize("power", ["On", "Off", "on", "off"])
def test_two_resource_signature_recognizes_hood(power):
    resources = _hood_resources()
    resources["/power/vs/0"][_POWER] = power
    resources.pop("/connected/vs/0")
    registry = for_device_by_resources(resources)
    assert registry is not None and registry.name == "range_hood"
    _, state = _bind(resources)
    assert state["power_state"] is (power.lower() == "on")


@pytest.mark.parametrize(
    "power_rep",
    [None, {}, {_POWER: None}, {_POWER: ""}, {_POWER: "Standby"}, {_POWER: True}],
    ids=["missing", "empty", "null", "empty_value", "unknown_value", "wrong_type"],
)
def test_mode_signature_alone_does_not_identify_hood(power_rep):
    resources = _hood_resources()
    if power_rep is None:
        resources.pop("/power/vs/0")
    else:
        resources["/power/vs/0"] = power_rep
    assert for_device_by_resources(resources) is None


@pytest.mark.parametrize("missing", ["BTState", "FanSpeed", "Lamp"])
def test_incomplete_mode_signature_does_not_identify_hood(missing):
    resources = _hood_resources()
    resources["/mode/vs/0"][_OPTIONS] = [
        option
        for option in resources["/mode/vs/0"][_OPTIONS]
        if not option.startswith(f"{missing}_")
    ]
    assert for_device_by_resources(resources) is None


def test_unpaired_bridge_has_diagnostics_without_physical_hood_entities():
    resources = _load_device(_UNPAIRED)
    registry = resolve(resources)
    assert registry is not None and registry.name == "range_hood"
    bound, state = _bind(resources)
    assert state == {
        "paired_hood_model": "NULL",
        "hood_bluetooth_state": "Idle",
        "hood_bluetooth_enabled": False,
        "hood_interface_available": True,
        "remote_control": True,
        "alarm_code": "none",
    }
    assert not _has_live_primary_entity(bound, state)


def test_paired_hood_maps_reported_fields_without_commands():
    bound, state = _bind(_hood_resources())
    assert state == {
        "paired_hood_model": "SYNTHETIC_HOOD",
        "hood_bluetooth_state": "Connected",
        "hood_bluetooth_enabled": True,
        "paired_hood_fan_speed": 2,
        "paired_hood_light": True,
        "hood_shutoff_timer_set": 10,
        "hood_shutoff_timer_current": 5,
        "hood_shutoff_timer_state": "Run",
        "power_state": False,
        "hood_interface_available": True,
        "remote_control": True,
        "diagnosis_status": "ready",
    }
    assert all(isinstance(b.desc, (SensorDesc, BinarySensorDesc)) for b in bound)
    assert _has_live_primary_entity(bound, state)


@pytest.mark.parametrize(
    ("missing", "entity_key"),
    [
        ("Bluetooth", "hood_bluetooth_enabled"),
        ("ShutOffTimerSet", "hood_shutoff_timer_set"),
        ("ShutOffTimerCurrent", "hood_shutoff_timer_current"),
        ("ShutOffTimerState", "hood_shutoff_timer_state"),
    ],
)
def test_absent_optional_hood_field_does_not_create_entity(missing, entity_key):
    resources = _hood_resources()
    resources["/mode/vs/0"][_OPTIONS] = [
        option
        for option in resources["/mode/vs/0"][_OPTIONS]
        if not option.startswith(f"{missing}_")
    ]
    _, state = _bind(resources)
    assert entity_key not in state
    assert "paired_hood_fan_speed" in state
    assert "paired_hood_light" in state


def test_captured_cooktop_keeps_its_burner_entities():
    resources = _load_device("cooktop_tp6x_ct_16k_8888")
    registry = resolve(resources)
    assert registry is not None and registry.name == "cooktop"
    _, state = _bind(resources)
    assert {key for key in state if key.startswith("burner_") and key.endswith("_state")} == {
        "burner_0_state",
        "burner_1_state",
        "burner_3_state",
        "burner_4_state",
        "burner_5_state",
    }
    assert not any(key.startswith(("hood_", "paired_hood_")) for key in state)

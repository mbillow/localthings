"""Read-only capability coverage for the TP6X_CT_16K legacy cooktop.

Burners keep their protocol slots (0, 1, 3, 4, 5); pan sizes occur only on 3 and 5.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.legacy_http import table_for, to_resources
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SensorDesc
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
    assert not any(key.startswith("burner_2_") for key in state)


def test_only_slots_3_and_5_report_pan_size():
    state = _state()
    assert state["burner_3_pan_size"] == "idle"
    assert state["burner_5_pan_size"] == "single"
    assert "burner_0_pan_size" not in state
    assert "burner_1_pan_size" not in state
    assert "burner_4_pan_size" not in state


@pytest.mark.parametrize("name", [NAME, "cooktop", "cooktop_nv9300k", "cooktop_nv8000t"])
def test_burner_entities_keep_their_slot_numbers(name):
    resources = _load_device(name)
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    by_key = {entity.desc.key: entity for entity in bound}
    assert len(by_key) == len(bound)
    coordinator = cast(LocalThingsCoordinator, SimpleNamespace(device_key="TEST-SERIAL"))
    sync = LocalThingsEntity(coordinator, by_key["cooktop_sync_control"])
    assert sync.translation_key == "cooktop_sync_control"
    assert sync.unique_id == "localthings_TEST-SERIAL_cooktop_sync_control"
    for number in (0, 1, 3, 4, 5):
        for reading in ("state", "power_level", "hot_surface", "pan_size"):
            key = f"burner_{number}_{reading}"
            entity = LocalThingsEntity(coordinator, by_key[key])
            assert entity.translation_placeholders == {"number": str(number)}
            expected_translation = (
                "cooktop_ring_size"
                if name == NAME and reading == "pan_size"
                else f"burner_{reading}"
            )
            assert entity.translation_key == expected_translation
            assert entity.unique_id == f"localthings_TEST-SERIAL_{key}"


@pytest.mark.parametrize("sync", ["On", "Off"])
def test_sync_status_keeps_both_burners_independent(sync):
    """Synthetic readings check slot separation with either reported Sync status."""
    resources = _load_device(NAME)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    options[options.index("SyncFlex_Off")] = f"SyncFlex_{sync}"
    options[options.index("OperationState0_Ready")] = "OperationState0_Run"
    options[options.index("PowerLevel0_Off")] = "PowerLevel0_5"
    options[options.index("HotSurface0_Normal")] = "HotSurface0_Hot"
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["cooktop_sync_control"] is (sync == "On")
    assert state["burner_0_state"] == "Run"
    assert state["burner_0_power_level"] == "5"
    assert state["burner_0_hot_surface"] is True
    assert state["burner_1_state"] == "Ready"
    assert state["burner_1_power_level"] == "Off"
    assert state["burner_1_hot_surface"] is False
    assert all(isinstance(entity.desc, (SensorDesc, BinarySensorDesc)) for entity in bound)


def test_legacy_mode_options_surface():
    state = _state()
    assert state["cooktop_device_type"] == "NV9300K-/AA2"
    assert state["cooktop_paused"] is False
    assert state["cooktop_sync_control"] is False
    assert state["cooktop_flex_coil"] == "0"
    assert "main_timer_current" not in state
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


@pytest.mark.parametrize("name", [NAME, "cooktop", "cooktop_nv9300k", "cooktop_nv8000t"])
def test_options_array_cooktops_expose_only_read_only_entities(name):
    resources = _load_device(name)
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert bound
    assert all(isinstance(entity.desc, (SensorDesc, BinarySensorDesc)) for entity in bound)


def test_legacy_envelopes_bind_to_cooktop_sensors():
    """Constructed envelopes exercise family selection and translation together."""
    resources = to_resources(
        {
            "Operation": {"power": "Off", "kidsLock": "Run"},
            "Mode": {
                "options": [
                    "DeviceType_NV9300K-/AA2",
                    "OperationState0_Ready",
                    "OperationState1_Ready",
                    "PanSize3_Idle",
                    "MainTimerSet_0",
                ]
            },
            "Configuration": {"remoteControlEnabled": True},
            "Information": {"modelID": "TP6X_CT_16K", "description": "TP6X_CT_16K"},
            "Diagnosis": {"diagnosisStart": "Ready"},
            "Alarms": [],
        },
        table_for("TP6X_CT_16K"),
    )
    reg = resolve(resources)
    assert reg is not None and reg.name == "cooktop"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []
    state = flatten(bound, resources)
    assert state["power_state"] is False
    assert state["child_lock"] is False
    assert state["remote_control"] is True
    assert state["cooktop_device_type"] == "NV9300K-/AA2"
    assert state["burner_0_state"] == "Ready"
    assert state["burner_1_state"] == "Ready"
    assert state["burner_3_pan_size"] == "idle"
    assert state["main_timer_set"] == 0
    assert state["diagnosis_status"] == "ready"
    assert state["alarm_code"] == "none"
    assert "cooktop_paused" not in state
    assert "cooktop_sync_control" not in state
    assert "cooktop_flex_coil" not in state
    assert "diagnosis_start" not in state


@pytest.mark.parametrize(
    ("capture", "minutes", "timer_state", "size_3", "size_5", "active"),
    [
        ("timer_10_minutes", 10, "Run", "single", "single", False),
        ("timer_8_minutes", 8, "Run", "single", "single", False),
        ("timer_alert_snapshot", 0, "Ready", "single", "single", False),
        ("burner_3_triple_ready", 0, "Ready", "triple", "single", False),
        ("burner_3_triple_running", 0, "Ready", "triple", "single", True),
        ("burner_5_dual_running", 0, "Ready", "single", "dual", True),
    ],
)
def test_captured_timer_and_ring_changes(capture, minutes, timer_state, size_3, size_5, active):
    observations = json.loads(
        (Path(__file__).parent / "fixtures/legacy_cooktop_observations.json").read_text()
    )
    resources = _load_device(NAME)
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = observations[capture]
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert len({b.desc.key for b in bound}) == len(bound)
    state = flatten(bound, resources)
    assert state["main_timer_set"] == minutes
    assert "main_timer_current" not in state
    assert state["main_timer_state"] == timer_state
    assert state["burner_3_pan_size"] == size_3
    assert state["burner_5_pan_size"] == size_5
    assert state["any_burner_active"] is active
    assert state["cooktop_flex_coil"] == "0"


@pytest.mark.parametrize("name", [NAME, "cooktop", "cooktop_nv9300k", "cooktop_nv8000t"])
def test_timer_units_only_for_verified_legacy_model(name):
    resources = _load_device(name)
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    descriptions = {b.desc.key: b.desc for b in bound}
    timer = cast(SensorDesc, descriptions["main_timer_set"])
    if "cooktop_paused" in descriptions:
        assert descriptions["cooktop_paused"].enabled_default is (name != NAME)
    if name == NAME:
        assert (timer.unit, timer.device_class, timer.translation_key) == (
            "min",
            "duration",
            "cooktop_timer_remaining",
        )
        assert timer.enabled_default is True
        assert "main_timer_current" not in descriptions
    else:
        duplicate = cast(SensorDesc, descriptions["main_timer_current"])
        assert timer.unit is None
        assert timer.device_class is None
        assert timer.translation_key is None
        assert duplicate.entity_category is None
        assert duplicate.enabled_default is False


def test_unknown_ring_size_and_finished_status_are_not_discarded():
    resources = _load_device(NAME)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    options[options.index("PanSize3_Idle")] = "PanSize3_FutureSize"
    options[options.index("MainTimerState_Ready")] = "MainTimerState_Finished"
    reg = resolve(resources)
    assert reg is not None
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["burner_3_pan_size"] == "FutureSize"
    assert state["main_timer_state"] == "Finished"


@pytest.mark.parametrize("rep", [{}, {"href": "/mode/vs/0"}])
def test_missing_model_keeps_generic_timer_sensors(rep):
    resources = _load_device(NAME)
    resources["/mode/vs/0"] = rep
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    timers = [b.desc for b in bound if b.desc.key == "main_timer_set"]
    assert len(timers) == 1
    assert cast(SensorDesc, timers[0]).unit is None
    assert any(b.desc.key == "main_timer_current" for b in bound)


@pytest.mark.parametrize(
    ("board", "model"),
    [
        ("TP6X_CT_16K", "NV9300K-/AA1"),
        ("TP6X_CT_16K", "NV9300K-OTHER"),
        ("TP6X_CT_16K", "NV9300K"),
        ("TP6X_CT_16K", None),
        ("OTHER_BOARD", "NV9300K-/AA2"),
    ],
)
def test_unverified_model_keeps_original_units_labels_and_defaults(board, model):
    resources = _load_device(NAME)
    resources["/information/vs/0"]["x.com.samsung.da.modelNum"] = board
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    options.remove("DeviceType_NV9300K-/AA2")
    if model is not None:
        options.append(f"DeviceType_{model}")
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    descriptions = {b.desc.key: b.desc for b in bound}
    timer = cast(SensorDesc, descriptions["main_timer_set"])
    assert (timer.unit, timer.device_class, timer.translation_key) == (None, None, None)
    assert "main_timer_current" in descriptions
    assert descriptions["burner_3_pan_size"].translation_key == "burner_pan_size"
    assert descriptions["cooktop_paused"].enabled_default is True
    assert descriptions["cooktop_flex_coil"].enabled_default is True

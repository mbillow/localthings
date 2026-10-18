"""TP6X dryer reads from diagnostics and rejection of unmeasured writes."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http import (
    PREFIX,
    TP6X_DRYER,
    batch_steps,
    is_mapped,
    table_for,
    to_resources,
    to_write,
    unmapped_wrappers,
)
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SensorDesc

FIXTURES = Path(__file__).parent / "fixtures"


def _bodies(name="dryer_tp6x_8888"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["bodies"]


def _discover(bodies):
    resources = to_resources(bodies, table_for("TP6X_DRYER"))
    registry = resolve(resources)
    assert registry is not None and registry.name == "dryer"
    unbound = []
    bound = discover(resources, registry.capabilities, log=unbound.append)
    assert unbound == []
    return bound, resources, flatten(bound, resources)


def test_idle_capture_gets_readable_entities():
    assert is_mapped("TP6X_DRYER")
    assert table_for("TP6X_DRYER") is TP6X_DRYER
    bound, resources, state = _discover(_bodies())
    assert state["machine_state"] == "idle"
    assert state["cycle_active"] is False
    assert state["completion_minutes"] == 169
    assert state["progress_percentage"] == 0
    assert state["finish_time"] is None
    assert state["remote_control"] is True
    assert state["power_switch"] is True
    assert state["legacy_dryer_cycle_code"] == "9A"
    assert state["legacy_dryer_dry_level"] == "2"
    assert state["legacy_dryer_dry_time"] == "00:00:00"
    assert state["legacy_dryer_wrinkle_prevent"] is False
    assert state["legacy_dryer_damp_alert"] is False
    assert all(isinstance(entity.desc, (SensorDesc, BinarySensorDesc)) for entity in bound)
    assert not any("kwh" in key or "watts" in key for key in state)
    assert "/washer/vs/0" in resources


def test_real_cooling_capture_gets_readable_entities():
    """Issue #646 reports Cooling even though supportedProgress omits it."""
    bodies = _bodies("dryer_tp6x_dv80m8214aw_cooling_8888")
    bound, _, state = _discover(bodies)
    assert state["machine_state"] == "active"
    assert state["cycle_active"] is True
    assert state["progress"] == "cooling"
    assert state["completion_minutes"] == 6
    assert state["progress_percentage"] == 82
    assert state["finish_time"] is not None
    assert state["remote_control"] is True
    assert state["legacy_dryer_cycle_code"] == "96"
    assert state["legacy_dryer_dry_level"] == "None"
    assert state["legacy_dryer_dry_time"] == "00:30:00"
    assert all(isinstance(entity.desc, (SensorDesc, BinarySensorDesc)) for entity in bound)
    golden = json.loads((FIXTURES / "golden/dryer_tp6x_8888.json").read_text())
    assert sorted(state) == golden["state_keys"]


@pytest.mark.parametrize(
    ("machine", "phase", "active", "display_state", "display_phase"),
    [
        ("Run", "Drying", True, "active", "drying"),
        ("Run", "Cooling", True, "active", "cooling"),
        ("Pause", "Drying", False, "pause", "idle"),
        ("Run", "Finish", False, "active", "finish"),
    ],
)
def test_cycle_transitions(machine, phase, active, display_state, display_phase):
    bodies = deepcopy(_bodies())
    bodies["Operation"].update(
        state=machine, progress=phase, remainingTime="00:01:00", progressPercentage=97
    )
    bound, _, state = _discover(bodies)
    assert state["machine_state"] == display_state
    assert state["progress"] == display_phase
    assert state["cycle_active"] is active
    if phase == "Finish":
        assert state["completion_minutes"] == 0
        assert state["finish_time"] is None
        percentage = next(entity for entity in bound if entity.desc.key == "progress_percentage")
        rep = to_resources(bodies, TP6X_DRYER)[percentage.href]
        assert isinstance(percentage.desc, SensorDesc)
        assert percentage.desc.sticky_fn is not None
        assert percentage.desc.sticky_value_fn is not None
        assert percentage.desc.sticky_fn(rep)
        assert percentage.desc.sticky_value_fn(rep) == 100
    elif active:
        assert state["finish_time"] is not None
        assert state["progress_percentage"] == 97


def test_smart_control_off_is_reported():
    bodies = _bodies()
    bodies["Configuration"]["remoteControlEnabled"] = False
    assert _discover(bodies)[2]["remote_control"] is False


@pytest.mark.parametrize(
    ("wrapper", "field", "key"),
    [
        ("Operation", "power", "power_switch"),
        ("Washer", "wrinklePrevent", "legacy_dryer_wrinkle_prevent"),
    ],
)
@pytest.mark.parametrize(("value", "expected"), [("On", True), ("Off", False)])
def test_reported_flags_keep_their_polarity(wrapper, field, key, value, expected):
    bodies = _bodies()
    bodies[wrapper][field] = value
    assert _discover(bodies)[2][key] is expected


@pytest.mark.parametrize(
    ("wrapper", "field", "key"),
    [
        ("Operation", "power", "power_switch"),
        ("Washer", "wrinklePrevent", "legacy_dryer_wrinkle_prevent"),
    ],
)
@pytest.mark.parametrize("value", [None, "NotSupported", [], {}, 0])
def test_unrecognized_flags_stay_unknown(wrapper, field, key, value):
    bodies = _bodies()
    bodies[wrapper][field] = value
    assert _discover(bodies)[2].get(key) is None


@pytest.mark.parametrize(
    ("wrapper", "field", "key"),
    [
        ("Operation", "power", "power_switch"),
        ("Washer", "wrinklePrevent", "legacy_dryer_wrinkle_prevent"),
    ],
)
def test_missing_flags_stay_unknown(wrapper, field, key):
    bodies = _bodies()
    del bodies[wrapper][field]
    assert _discover(bodies)[2].get(key) is None


@pytest.mark.parametrize(
    ("value", "expected"), [("Enable", True), ("Disable", False), ("NotSupported", None)]
)
def test_damp_alert_distinguishes_off_from_unknown(value, expected):
    bodies = _bodies()
    bodies["Mode"]["options"] = ["Course_9A", f"MixedLoadBell_{value}"]
    assert _discover(bodies)[2]["legacy_dryer_damp_alert"] is expected


def test_energy_database_pointer_stays_a_visible_gap():
    assert unmapped_wrappers(_bodies(), TP6X_DRYER) == ["EnergyConsumption"]


@pytest.mark.parametrize(
    ("href", "patch"),
    [
        ("/operational/state/vs/0", {PREFIX + "state": "Run"}),
        ("/power/vs/0", {PREFIX + "power": "Off"}),
        ("/washer/vs/0", {PREFIX + "dryLevel": "3"}),
        ("/course/vs/0", {PREFIX + "options": ["Course_9A"]}),
        ("/remotectrl/vs/0", {PREFIX + "remoteControlEnabled": True}),
    ],
)
def test_direct_and_batch_commands_cannot_be_translated(href, patch):
    assert to_write([(href, patch)], TP6X_DRYER) == {"Device": {}}
    assert batch_steps([{"href": href, "rep": patch}], TP6X_DRYER) is None


def test_captured_entities_match_golden():
    golden = json.loads((FIXTURES / "golden/dryer_tp6x_8888.json").read_text())
    assert sorted(_discover(_bodies())[2]) == golden["state_keys"]


def test_missing_optional_values_do_not_invent_damp_alert():
    bodies = _bodies()
    bodies["Mode"]["options"] = ["Course_9A"]
    assert "legacy_dryer_damp_alert" not in _discover(bodies)[2]


@pytest.mark.parametrize(
    "body",
    [
        {PREFIX + "state": "Run"},
        [{"href": "/operational/state/vs/0", "rep": {PREFIX + "state": "Run"}}],
    ],
)
def test_transport_rejects_writes_without_contacting_the_appliance(monkeypatch, body):
    from custom_components.localthings.legacy_http_transport import LegacyHttpTransport

    transport = LegacyHttpTransport(
        "192.0.2.1",
        8888,
        cert_pem="unused",
        key_pem="unused",
        token="unused",
        family="TP6X_DRYER",
    )
    requests = []
    monkeypatch.setattr(transport, "_request", lambda *args, **kwargs: requests.append(args))
    path = ["device", "0"] if isinstance(body, list) else ["operational", "state", "vs", "0"]
    code, _ = transport.write(path, body, timeout=1)
    assert code == 0x84
    assert requests == []

"""Tests for the DA_DF_TP1_21_COMMON AirDresser (model DF3000B, issue #208).

Another board generation routed into the same air_dresser registry as
issue #162's DA_DF_A51_20_COMMON and issue #157's DA_DF_TP2_20_COMMON.
Exercises the one thing this board reports that neither of those does:
a populated /buzzersound/vs/0 (laundry.BUZZER_SOUND) -- the reporter's
actual ask ("all cycles are represented by code") is a course-code
labelling problem, not a coverage one; this board's course table isn't
identified yet either (same as #162's), so 'cycle' still renders as the
raw code until a reporter can map codes to names in the SmartThings app.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import air_dresser, for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc
from tests.conftest import _load_device


def _air_dresser():
    resources = _load_device("air_dresser_tp1_21")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    return reg, resources


def _state():
    reg, resources = _air_dresser()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_air_dresser_registry():
    reg, _ = _air_dresser()
    assert reg is not None and reg.name == "air_dresser"


def test_no_unbound_hrefs():
    reg, resources = _air_dresser()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_buzzer_sound_present_and_writable():
    """Issue #208's actual coverage gap: /buzzersound/vs/0 was unbound on
    this board before laundry.BUZZER_SOUND was added to the registry."""
    state = _state()
    assert state["buzzer_sound_enabled"] is True
    assert "finish_sound" not in state  # supportedFinishSound absent -- self-gated off

    desc = next(
        e
        for e in air_dresser.REGISTRY.capabilities["/buzzersound/vs/0"][0].entities
        if e.key == "buzzer_sound_enabled" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    _, resources = _air_dresser()
    result = desc.write_fn("Off", resources["/buzzersound/vs/0"])
    assert result is not None
    path, body = result
    assert path == ["buzzersound", "vs", "0"]
    assert body == {"setBuzzerSound": "Off"}


def test_course_translation_key_falls_back_to_cycle_for_unidentified_table():
    """Same as issue #162's board: Table_00's course codes aren't
    identified yet, so the select renders raw codes rather than names."""
    _, resources = _air_dresser()
    desc = air_dresser.REGISTRY.capabilities["/course/vs/0"][0].entities[0]
    assert desc.translation_key(resources) == "cycle"


def test_sanitize_present_and_toggles():
    state = _state()
    assert state["sanitize"] is False

    desc = next(
        e
        for e in air_dresser.REGISTRY.capabilities["/airdresseroption/sanitize/vs/0"][0].entities
        if e.key == "sanitize" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn("On", {})
    assert result is not None
    path, body = result
    assert path == ["airdresseroption", "sanitize", "vs", "0"]
    assert body == {"x.com.samsung.da.sanitize": "On"}

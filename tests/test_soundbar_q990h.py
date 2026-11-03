"""Confirmed HW-Q990H contracts, reconstructed without per-device data."""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc, SwitchDesc
from tests.conftest import FIXTURES, _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")
MODERN = "/sec/networkaudio/av/soundsetting"
REAR = "/sec/networkaudio/surroundspeaker"
P = "x.com.samsung.networkaudio."


def _bound(resources):
    registry = resolve(resources, device_types=DEVICE_TYPES)
    assert registry is not None and registry.name == "soundbar"
    unbound: list[str] = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    assert unbound == []
    return bound


def _included(resources):
    coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
        ),
    )
    return [bound for bound in _bound(resources) if _is_included(bound, coordinator)]


def _mixed():
    resources = _load_device("soundbar_q990h")
    resources.update(
        {
            "/sec/networkaudio/activeVoiceAmplifier": {P + "activeVoiceAmplifier": 1},
            "/sec/networkaudio/spacefitSound": {P + "spacefitSound": 0},
        }
    )
    return resources


def test_q990h_contract_fixture_reads_confirmed_value_shapes():
    resources = _load_device("soundbar_q990h")
    state = flatten(_bound(resources), resources)
    assert state == {
        "active_voice_amplifier": False,
        "spacefit_sound": True,
        "rear_speaker_position": "Rear",
    }


def test_both_resource_forms_create_one_native_control_per_feature():
    resources = _mixed()
    included = _included(resources)
    for key in ("active_voice_amplifier", "spacefit_sound"):
        controls = [bound for bound in included if bound.desc.key == key]
        assert len(controls) == 1
        assert controls[0].href == MODERN
    state = flatten(_bound(resources), resources)
    assert state["active_voice_amplifier"] is False
    assert state["spacefit_sound"] is True


@pytest.mark.parametrize("value", [None, -1, 2, "on", True])
def test_invalid_modern_field_preserves_only_that_legacy_control(value):
    resources = _mixed()
    resources[MODERN]["activevoiceamplifier"] = value
    included = _included(resources)
    controls = [bound for bound in included if bound.desc.key == "active_voice_amplifier"]
    assert len(controls) == 1
    assert controls[0].href == "/sec/networkaudio/activeVoiceAmplifier"
    state = flatten(_bound(resources), resources)
    assert state["active_voice_amplifier"] is True
    assert state["spacefit_sound"] is True


def test_missing_modern_field_does_not_hide_the_other_setting():
    resources = _mixed()
    del resources[MODERN]["spacefitsound"]
    included = _included(resources)
    controls = [bound for bound in included if bound.desc.key == "spacefit_sound"]
    assert len(controls) == 1
    assert controls[0].href == "/sec/networkaudio/spacefitSound"
    assert flatten(_bound(resources), resources)["active_voice_amplifier"] is False


@pytest.mark.parametrize(
    "key,field",
    [("active_voice_amplifier", "activevoiceamplifier"), ("spacefit_sound", "spacefitsound")],
)
@pytest.mark.parametrize("value,wire", [("On", 1), ("Off", 0)])
def test_modern_switch_writes_the_confirmed_unprefixed_field(key, field, value, wire):
    resources = _load_device("soundbar_q990h")
    bound = next(bound for bound in _included(resources) if bound.desc.key == key)
    assert isinstance(bound.desc, SwitchDesc)
    write: Any = bound.desc.write_fn
    assert write(value, resources[MODERN], bound.href) == (
        ["sec", "networkaudio", "av", "soundsetting"],
        {field: wire},
    )


@pytest.mark.parametrize("value", ["Rear", "Front"])
def test_rear_placement_uses_device_options_and_the_confirmed_write(value):
    resources = _load_device("soundbar_q990h")
    bound = next(
        bound for bound in _included(resources) if bound.desc.key == "rear_speaker_position"
    )
    assert isinstance(bound.desc, SelectDesc)
    assert bound.desc.options_field is not None
    assert value in resources[REAR][bound.desc.options_field]
    write: Any = bound.desc.write_fn
    assert write(value, resources[REAR], bound.href) == (
        ["sec", "networkaudio", "surroundspeaker"],
        {P + "currentRearPosition": value},
    )


@pytest.mark.parametrize(
    "changes", [{P + "currentRearPosition": "none"}, {P + "rearPositionList": []}]
)
def test_unsupported_rear_placement_does_not_create_an_entity(changes):
    resources = _load_device("soundbar_q990h")
    resources[REAR].update(changes)
    assert all(bound.desc.key != "rear_speaker_position" for bound in _included(resources))
    assert "rear_speaker_position" not in flatten(_bound(resources), resources)


def test_existing_s61b_entities_and_golden_stay_unchanged():
    resources = _load_device("soundbar_s61b")
    state = flatten(_bound(resources), resources)
    golden = json.loads((FIXTURES / "golden/soundbar_s61b.json").read_text())
    assert sorted(state) == golden["state_keys"]
    assert "rear_speaker_position" not in state
    old = deepcopy(resources)
    old[MODERN] = {}
    assert flatten(_bound(old), old) == state

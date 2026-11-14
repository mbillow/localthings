"""Buzzer discovery must not turn missing vocabulary into a volume control."""

from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.laundry import BUZZER_SOUND
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc

HREF = "/buzzersound/vs/0"


@pytest.mark.parametrize(
    "rep,keys",
    [
        ({}, {"buzzer_sound"}),
        ({"href": HREF}, {"buzzer_sound"}),
        ({"supportedFinishSound": ["A"], "setFinishSound": "A"}, set()),
        ({"setBuzzerSound": "On"}, set()),
        ({"supportedBuzzerSound": ["On", "Off"]}, set()),
        ({"setBuzzerSound": "On", "supportedBuzzerSound": []}, set()),
        ({"setBuzzerSound": "On", "supportedBuzzerSound": "On"}, set()),
        ({"setBuzzerSound": "On", "supportedBuzzerSound": [None]}, set()),
        ({"setBuzzerSound": "Other", "supportedBuzzerSound": ["On", "Off"]}, set()),
        ({"setBuzzerSound": "On", "supportedBuzzerSound": ["On", "ON", "Off"]}, set()),
        ({"setBuzzerSound": "On", "supportedBuzzerSound": ["On", "Off"]}, {"buzzer_sound_enabled"}),
        # The vocabulary is compared ignoring case, so the state is too.
        ({"setBuzzerSound": "on", "supportedBuzzerSound": ["On", "Off"]}, {"buzzer_sound_enabled"}),
        (
            {"setBuzzerSound": "Volume_Low", "supportedBuzzerSound": ["Volume_Low", "Volume_High"]},
            {"buzzer_sound"},
        ),
    ],
)
def test_buzzer_platform_requires_evidence(rep, keys):
    resources = {HREF: rep}
    bounds = discover(resources, {HREF: [BUZZER_SOUND]})
    coordinator = SimpleNamespace(
        discovery_resources=resources, discovery_canonical=lambda sub: resources
    )
    assert {
        b.desc.key
        for b in bounds
        if b.desc.key != "finish_sound"
        and _is_included(b, cast(LocalThingsCoordinator, coordinator))
    } == keys


@pytest.mark.parametrize(
    "tokens,current,wanted", [(["on", "off"], "on", True), (["oN", "OFF"], "OFF", False)]
)
def test_boolean_wire_spelling_is_preserved(tokens, current, wanted):
    rep = {"supportedBuzzerSound": tokens, "setBuzzerSound": current}
    resources = {HREF: rep}
    bounds = discover(resources, {HREF: [BUZZER_SOUND]})
    assert flatten(bounds, resources)["buzzer_sound_enabled"] is wanted
    desc = next(b.desc for b in bounds if b.desc.key == "buzzer_sound_enabled")
    assert isinstance(desc, SwitchDesc)
    assert desc.write_fn is not None
    assert desc.write_fn("On", rep) == (["buzzersound", "vs", "0"], {"setBuzzerSound": tokens[0]})
    assert desc.write_fn("Off", rep) == (["buzzersound", "vs", "0"], {"setBuzzerSound": tokens[1]})


@pytest.mark.parametrize("parent_boolean", [True, False])
def test_composite_siblings_choose_their_own_buzzer_platform(parent_boolean):
    from custom_components.localthings.registry.subdevices import MAIN, Subdevice, canonical_view

    child = Subdevice(kind="prefixed", key="child", seed_path=("child", "device", "0"))
    boolean = {"supportedBuzzerSound": ["On", "Off"], "setBuzzerSound": "Off"}
    volume = {"supportedBuzzerSound": ["Volume_Low", "Volume_High"], "setBuzzerSound": "Volume_Low"}
    resources = {
        HREF: boolean if parent_boolean else volume,
        child.to_actual(HREF): volume if parent_boolean else boolean,
    }
    bounds = []
    for sub in (MAIN, child):
        bounds.extend(
            discover(canonical_view(sub, resources, [child]), {HREF: [BUZZER_SOUND]}, subdevice=sub)
        )
    state = flatten(bounds, resources)
    if parent_boolean:
        assert state["buzzer_sound_enabled"] is False
        assert state["subdevice_child_buzzer_sound"] == "Volume_Low"
        assert "subdevice_child_buzzer_sound_enabled" not in state
    else:
        assert state["buzzer_sound"] == "Volume_Low"
        assert state["subdevice_child_buzzer_sound_enabled"] is False
        assert "subdevice_child_buzzer_sound" not in state

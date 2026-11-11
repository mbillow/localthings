"""Drum lamps share the course-token contract on washers and dryers."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.dryer import DRYER_COURSE
from custom_components.localthings.registry.capabilities.washer import WASHER_COURSE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import LightDesc


@pytest.mark.parametrize("capability", [WASHER_COURSE, DRYER_COURSE], ids=["washer", "dryer"])
@pytest.mark.parametrize("value,expected", [("On", True), ("Off", False)])
def test_drum_light_reads_lamp_and_writes_only_its_token(capability, value, expected):
    desc = next(entity for entity in capability.entities if entity.key == "drum_light")
    assert isinstance(desc, LightDesc)
    assert desc.entity_category is None
    assert desc.dynamic_presence
    assert not desc.needs_remote_control
    assert not desc.supports_brightness
    rep = {"x.com.samsung.da.options": ["Course_16", f"DrumLight_{value}", "WrinklePrevent_On"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {capability.href: rep})
    bound = BoundEntity(href=capability.href, capability=capability, desc=desc)
    assert flatten([bound], {capability.href: rep})["drum_light"] is expected
    assert desc.write_fn is not None
    assert desc.write_fn(expected, rep) == (
        ["course", "vs", "0"],
        {"x.com.samsung.da.options": [f"DrumLight_{value}"]},
    )
    assert desc.write_fn("Unsupported", rep) is None


@pytest.mark.parametrize("capability", [WASHER_COURSE, DRYER_COURSE], ids=["washer", "dryer"])
@pytest.mark.parametrize("rep", [{}, {"x.com.samsung.da.options": ["Course_16"]}])
def test_model_without_drum_light_token_does_not_get_a_lamp(capability, rep):
    desc = next(entity for entity in capability.entities if entity.key == "drum_light")
    assert desc.exists_fn is not None
    assert not desc.exists_fn(rep, {capability.href: rep})

"""Tests for verified refrigerator maintenance and display controls."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import refrigerator
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from tests.conftest import _load_device


def _discover_refrigerator():
    resources = _load_device("refrigerator")
    bound = discover(
        resources,
        refrigerator.REGISTRY.capabilities,
        refrigerator.REGISTRY.pattern_capabilities,
    )
    return resources, bound


def test_verified_fixture_exposes_the_remaining_controls():
    resources, bound = _discover_refrigerator()
    keys = {item.desc.key for item in bound}
    state = flatten(bound, resources)

    assert {
        "welcome_lighting_proximity",
        "welcome_lighting_sense_level",
        "night_lighting_schedule",
    } <= keys
    assert state["welcome_lighting_proximity"] == "far"
    assert state["welcome_lighting_sense_level"] == "near"
    assert state["night_lighting_schedule"] is True


def test_proximity_select_uses_only_advertised_levels():
    resources, _bound = _discover_refrigerator()
    desc = next(
        item
        for item in fridge.WELCOME_LIGHTING.entities
        if item.key == "welcome_lighting_proximity"
    )
    assert isinstance(desc, SelectDesc)
    assert callable(desc.options)
    assert desc.display_fn is not None
    assert desc.write_fn is not None
    rep = resources["/proximity/vs/0"]

    assert desc.options(resources) == ["1", "2", "3"]
    assert desc.display_fn("2", resources) == "middle"
    assert desc.value_fn(rep["currentLevel"]) == "far"
    assert desc.write_fn("middle", rep) == (
        ["proximity", "vs", "0"],
        {"currentLevel": "2"},
    )
    assert desc.write_fn("2", rep) == (
        ["proximity", "vs", "0"],
        {"currentLevel": "2"},
    )
    assert desc.write_fn("nearest", rep) is None
    assert desc.write_fn("bogus", rep) is None
    assert desc.write_fn("near", {"supportedLevels": None}) is None


def test_welcome_lighting_sense_level_is_a_default_disabled_diagnostic():
    resources, _bound = _discover_refrigerator()
    desc = next(
        item
        for item in fridge.WELCOME_LIGHTING.entities
        if item.key == "welcome_lighting_sense_level"
    )
    assert isinstance(desc, SensorDesc)
    assert callable(desc.options)

    assert desc.enabled_default is False
    assert desc.entity_category == "diagnostic"
    assert desc.options(resources) == ["near", "far"]
    assert desc.value_fn("0") == "nearest"
    assert desc.value_fn("1") == "near"
    assert desc.value_fn("3") == "far"
    assert desc.value_fn("unexpected") is None


def test_night_lighting_schedule_switch_writes_only_the_enable_field():
    desc = next(
        item
        for item in fridge.CABINET_LIGHT_ENHANCED.entities
        if item.key == "night_lighting_schedule"
    )
    assert isinstance(desc, SwitchDesc)
    assert desc.write_fn is not None

    assert desc.value_fn("On") is True
    assert desc.value_fn("Off") is False
    assert desc.write_fn("On", {}) == (
        ["cabinet", "light", "enhanced", "vs", "0"],
        {"light.control.status": "On"},
    )
    assert desc.write_fn("Off", {}) == (
        ["cabinet", "light", "enhanced", "vs", "0"],
        {"light.control.status": "Off"},
    )
    assert desc.write_fn("invalid", {}) is None

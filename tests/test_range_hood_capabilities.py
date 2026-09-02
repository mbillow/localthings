"""Tests for the AHD-WW-TP1-22 range-hood profile."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import range_hood
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    BinarySensorDesc,
    ButtonDesc,
    FanDesc,
    SelectDesc,
    SwitchDesc,
)
from tests.conftest import _load_device


def _hood():
    resources = _load_device("range_hood")
    info = resources["/information/vs/0"]
    registry = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return registry, resources


def _state():
    registry, resources = _hood()
    bound = discover(
        resources,
        registry.capabilities,
        registry.pattern_capabilities,
    )
    return flatten(bound, resources)


def test_ahd_model_resolves_to_range_hood_registry():
    registry, _ = _hood()
    assert registry is not None
    assert registry.name == "range_hood"


def test_range_hood_registry_covers_dawit_generation_hrefs():
    """HOOD_STATUS/HOOD_SPEC (issue #433's built-in combi vent hood) are
    generic vent-hood capabilities defined in capabilities/range_hood.py,
    not microwave-specific -- a standalone hood of the same board
    generation should be covered too, even with no such dump yet."""
    registry, _ = _hood()
    assert "/hood/status/vs/0" in registry.capabilities
    assert "/hood/spec/vs/0" in registry.capabilities


def test_range_hood_has_no_unbound_hrefs():
    registry, resources = _hood()
    unbound = []
    discover(
        resources,
        registry.capabilities,
        registry.pattern_capabilities,
        log=unbound.append,
    )
    assert unbound == []


def test_range_hood_fixture_values():
    state = _state()
    assert state["fan"] == "14"
    assert state["alarm_code"] == "none"
    assert state["automatic_operation"] is False
    assert state["lamp"] is False
    assert state["lamp_brightness"] == "2"
    assert state["hood_filter_usage"] == 100
    assert state["hood_filter_status"] == "wash"
    assert state["energy_kwh"] == 165.62
    assert state["clean_level"] == 2
    assert state["dust"] == 18
    assert state["fine_dust"] == 14
    assert state["super_fine_dust"] == 9
    assert state["periodic_air_sensing"] is False
    assert state["air_sensing_state"] == "NonProcessing"
    assert state["last_air_sensing_level"] == "Kr2"
    assert state["after_run_active"] is False
    assert state["after_run_progress"] == 0


def test_one_composite_fan_is_bound():
    registry, resources = _hood()
    bound = discover(
        resources,
        registry.capabilities,
        registry.pattern_capabilities,
    )
    fans = [entity for entity in bound if isinstance(entity.desc, FanDesc)]
    assert len(fans) == 1
    assert fans[0].href == "/hood/fanspeed/vs/0"


def test_fan_write_contract():
    desc = next(e for e in range_hood.HOOD_FAN.entities if isinstance(e, FanDesc))
    rep = {
        "x.com.samsung.da.hood.supportedFanSpeed": ["14", "15", "16", "17"],
    }
    assert desc.write_fn is not None
    assert desc.write_fn(("power", True), rep) == (
        ["power", "0"],
        {"value": True},
    )
    assert desc.write_fn(("power", False), rep) == (
        ["power", "0"],
        {"value": False},
    )
    assert desc.write_fn(("power", True, "/power/vs/0"), rep) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "On"},
    )
    assert desc.write_fn(("power", False, "/power/vs/0"), rep) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "Off"},
    )
    assert desc.write_fn(("power", True, "/not-a-power-resource"), rep) is None
    assert desc.write_fn(("speed", "16"), rep) == (
        ["hood", "fanspeed", "vs", "0"],
        {"x.com.samsung.da.hood.fanSpeed": "16"},
    )
    assert desc.write_fn(("speed", "99"), rep) is None

    rep_fallback = {
        "x.com.samsung.da.hood.settableMinFanSpeed": "0",
        "x.com.samsung.da.hood.settableMaxFanSpeed": "3",
    }
    assert desc.write_fn(("speed", "2"), rep_fallback) == (
        ["hood", "fanspeed", "vs", "0"],
        {"x.com.samsung.da.hood.fanSpeed": "2"},
    )
    assert desc.write_fn(("speed", "4"), rep_fallback) is None


def test_lamp_write_contract():
    lamp = next(e for e in range_hood.HOOD_LAMP.entities if isinstance(e, SwitchDesc))
    brightness = next(e for e in range_hood.HOOD_LAMP.entities if isinstance(e, SelectDesc))
    rep = {"x.com.samsung.lamp.range": ["1", "2"]}
    assert lamp.write_fn is not None
    assert brightness.write_fn is not None
    assert lamp.write_fn("On", rep) == (
        ["hood", "lamp", "vs", "0"],
        {"x.com.samsung.lamp.power": "On"},
    )
    assert lamp.write_fn("Off", rep) == (
        ["hood", "lamp", "vs", "0"],
        {"x.com.samsung.lamp.power": "Off"},
    )
    assert brightness.write_fn("1", rep) == (
        ["hood", "lamp", "vs", "0"],
        {"x.com.samsung.lamp.current": "1"},
    )
    assert brightness.write_fn("2", rep) == (
        ["hood", "lamp", "vs", "0"],
        {"x.com.samsung.lamp.current": "2"},
    )
    assert brightness.write_fn("Unsupported", rep) is None


def test_after_run_cancel_write_contract():
    """issue #147: /afterrun/vs/0 was previously unbound. runningCancel's
    only observed value is the command name itself, so cancel is modeled as
    a ButtonDesc that writes that value back."""
    cancel = next(e for e in range_hood.AFTER_RUN.entities if isinstance(e, ButtonDesc))
    assert cancel.write_fn is not None
    assert cancel.write_fn("Cancel", {}) == (
        ["afterrun", "vs", "0"],
        {"x.com.samsung.da.runningCancel": "Cancel"},
    )


def test_after_run_active_reads_on_off():
    active = next(e for e in range_hood.AFTER_RUN.entities if isinstance(e, BinarySensorDesc))
    assert active.value_fn is not None
    assert active.value_fn("On") is True
    assert active.value_fn("Off") is False

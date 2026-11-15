"""Bespoke Jet Bot Combo (JETBOT_COMBOT_9X00_24K, #838): the R7040's token
model plus mop and station settings, and a dock with its own resources."""

import copy
from typing import cast

import pytest
from homeassistant.components.vacuum import VacuumEntityFeature

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import robot_vacuum as rv
from custom_components.localthings.registry.capabilities.common import merge_options_field
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import VacuumDesc
from custom_components.localthings.vacuum import LocalThingsVacuum
from tests.conftest import _load_device
from tests.test_robot_vacuum_capabilities import _FakeCoordinator

DEVICE_TYPES = ("oic.wk.d", "oic.d.robotcleaner")
MODES = "x.com.samsung.da.modes"


def _resources():
    return _load_device("robot_vacuum_jetbot_combo")


def _bind(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "robot_vacuum"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, unbound


def _with_modes(resources, *tokens):
    resources = copy.deepcopy(resources)
    rep = resources[rv.HREF_MODE]
    rep[MODES] = merge_options_field(rep[MODES], list(tokens))
    return resources


def _desc(key, href=None):
    bound, _ = _bind(_resources())
    return next(b for b in bound if b.desc.key == key and href in (None, b.href))


def _write(key, payload):
    b = _desc(key)
    assert b.desc.write_fn is not None
    return b.desc.write_fn(payload, _resources()[b.href], b.href)


def test_zero_unbound_hrefs_and_the_docked_state():
    resources = _resources()
    bound, unbound = _bind(resources)
    assert unbound == []
    state = flatten(bound, resources)
    assert state["vacuum"] == "docked"
    assert state["battery"] == 100
    assert state["battery_health"] == 100
    assert state["robot_status"] == "charged"
    assert state["battery_charging"] is False
    assert (state["cleaning_method"], state["water_level"], state["cleaning_option"]) == (
        "AtOnce",
        "Med",
        "Daily",
    )
    assert state["auto_empty"] is True
    assert state["mop_attached"] is True
    for key in (
        "dust_bag_full",
        "dock_filter",
        "motor_filter",
        "clean_water_tank",
        "dirty_water_tank",
        "station_water_leak",
        "do_not_disturb",
    ):
        assert state[key] is False, key
    assert state["child_lock"] is True  # unlocked
    assert state["sound_volume"] == 2


@pytest.mark.parametrize(
    ("control", "activity", "charging"),
    [
        ("Charged", "docked", False),
        ("FlexCharged", "docked", False),
        ("MopWashCharging", "docked", True),
        ("MopWash", "docked", False),
        ("MopDry", "docked", False),
        ("EmitDust", "docked", False),
        ("RechargeHoming", "returning", False),
        ("MopWashHoming", "returning", False),
        ("CreateMapPause", "paused", False),
        ("PatternMap", "cleaning", False),
        ("Exploring", "idle", False),
    ],
)
def test_control_states_by_shape(control, activity, charging):
    rep = _with_modes(_resources(), f"Control_{control}")[rv.HREF_MODE]
    assert rv.activity(rep) == activity
    assert rv.is_charging(rv.group_value(rep, rv.CONTROL)) is charging


async def test_spot_and_the_ultra_quiet_fan_speed():
    resources = _resources()
    bound, _ = _bind(resources)
    vacuum = next(b for b in bound if isinstance(b.desc, VacuumDesc))
    coordinator = _FakeCoordinator(resources)
    entity = LocalThingsVacuum(cast(LocalThingsCoordinator, coordinator), vacuum)
    assert VacuumEntityFeature.CLEAN_SPOT in entity.supported_features
    assert entity.fan_speed_list == ["turbo", "normal", "quiet", "ultra_quiet"]
    await entity.async_clean_spot()
    await entity.async_set_fan_speed("ultra_quiet")
    assert [p for _, p in coordinator.commands] == [
        ("Cleaning", "Spot"),
        ("Turbo", "UltraSilence"),
    ]


@pytest.mark.parametrize(
    ("key", "payload", "token"),
    [
        ("cleaning_method", "Mop", "CleaningMethod_Mop"),
        ("water_level", "High", "WaterLevel_High"),
        ("cleaning_option", "Intensive", "Option_Intensive"),
        ("auto_empty", "Off", "DustEmission_Off"),
        ("empty_dust_bin", "Start", "DustEmission_Start"),
    ],
)
def test_mode_group_writes_send_one_token(key, payload, token):
    assert _write(key, payload) == (["mode", "vs", "0"], {MODES: [token]})


def test_unsupported_group_values_are_refused():
    assert _write("water_level", "Flood") is None


def test_resource_writes():
    assert _write("robot_lighting", "Off") == (
        ["lighting", "vs", "0"],
        {"x.com.samsung.da.lighting": "Off"},
    )
    assert _write("find_robot", "Start") == (
        ["findrobotcleaner", "vs", "0"],
        {"x.com.samsung.da.findAlarm": "Start"},
    )


def test_child_lock_on_means_unlocked():
    # Not the R7040's Access_ lock on /mode/vs/0, which this robot doesn't gate in.
    value_fn = _desc("child_lock", "/child/lock/vs/0").desc.value_fn
    assert value_fn("False") is True
    assert value_fn("True") is False


def test_station_tanks_and_leak():
    station = _resources()["/station/vs/0"]
    status = station["x.com.samsung.da.mopStationStatus"]
    rep_fn = {k: _desc(k).desc.rep_fn for k in ("clean_water_tank", "station_water_leak")}
    for fn in rep_fn.values():
        assert fn is not None
    assert rep_fn["clean_water_tank"](station) is False
    bad = {
        **station,
        "x.com.samsung.da.mopStationStatus": {
            **status,
            "x.com.samsung.da.cleanWater": "Low",
            "x.com.samsung.da.waterLeak": "True",
        },
    }
    assert rep_fn["clean_water_tank"](bad) is True
    assert rep_fn["station_water_leak"](bad) is True

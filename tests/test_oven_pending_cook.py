"""Pending recipes survive entry order without changing appliance snapshots."""

import json
from copy import deepcopy
from itertools import permutations
from unittest.mock import AsyncMock

import pytest

from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_cook_start import _idle
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- installs the fake legacy transport
    _FakeConnection,
    _lower,
    _requests,
    _routes,
)


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
@pytest.mark.parametrize("order", list(permutations(("oven_mode", "oven_setpoint", "cook_time"))))
async def test_entry_order_and_reselection_preserve_each_cavity_recipe(
    hass, monkeypatch, index, divided, mode, order
):
    coordinator = await _discovered(hass, _routes(divided=divided))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    values = {"oven_mode": mode, "oven_setpoint": 400, "cook_time": 20}
    before = deepcopy(coordinator.last_resources)
    _FakeConnection.log.clear()

    for key in order:
        await coordinator.async_send_command(bound[key], values[key])
        if key != "oven_mode" and "oven_mode" not in order[: order.index(key)]:
            view = coordinator.entity_resources()[cavity.to_actual(cook.MODE_HREF)]
            assert view[PREFIX + "modes"] == ["NoOperation"]
    await coordinator.async_send_command(bound["oven_mode"], mode)

    assert LocalThingsNumber(coordinator, bound["oven_setpoint"]).native_value == 400
    assert LocalThingsNumber(coordinator, bound["cook_time"]).native_value == 20
    assert coordinator.last_resources == before
    assert not _requests()
    assert not any(
        held.values for other, held in coordinator._held_cooks.items() if other != cavity
    )

    send = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_put", send)
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    await coordinator.async_start_cooking(cavity)

    send.assert_awaited_once()
    path, body, href = send.call_args.args
    assert path == ["device", str(index)]
    assert href == f"/device/{index}"
    reps = {element["href"]: element["rep"] for element in body if "rep" in element}
    assert reps[cavity.to_actual(cook.MODE_HREF)] == {PREFIX + "modes": [mode]}
    assert (
        reps[cavity.to_actual(cook.TEMPERATURES_HREF)][PREFIX + "items"][0][PREFIX + "desired"]
        == "400"
    )
    assert reps[cavity.to_actual(cook.OPERATION_HREF)] == {
        PREFIX + "operationTime": "00:20:00",
        PREFIX + "state": "Run",
    }
    assert not _requests()
    assert not coordinator._held_cooks[cavity].values


def _without_default():
    resources = _idle("range_ne6516a")
    resources[cook.MODE_HREF].pop(PREFIX + "defaultMode", None)
    return resources


@pytest.mark.parametrize(
    "parameter,value,error",
    [
        (cook.PARAM_TEMPERATURE, 200, "cook_temperature_out_of_range"),
        (cook.PARAM_DURATION, 600, "cook_duration_out_of_range"),
    ],
)
def test_first_mode_revalidates_and_rejection_preserves_earlier_entries(parameter, value, error):
    resources = _without_default()
    specs = json.loads(resources[cook.MODE_HREF][cook.MODE_SPEC])
    for spec in specs:
        if spec["mode"] == "AirFryer":
            spec["timeMax"] = spec["timeDefault"] = "00:05:00"
    resources[cook.MODE_HREF][cook.MODE_SPEC] = specs
    held = cook.HeldCook()
    held.hold(parameter, value, resources)

    with pytest.raises(cook.CookStartError) as err:
        held.hold(cook.PARAM_MODE, "AirFryer", resources)

    assert err.value.key == error
    assert held.values == {parameter: value}
    held.hold(cook.PARAM_MODE, "Bake", resources)
    plan = held.plan(resources)
    assert getattr(plan, parameter) == value


def test_pre_mode_temperature_uses_the_chosen_modes_rounding():
    resources = _without_default()
    specs = json.loads(resources[cook.MODE_HREF][cook.MODE_SPEC])
    for spec in specs:
        if spec["mode"] == "AirFryer":
            spec["tempIntervalF"] = "10"
    resources[cook.MODE_HREF][cook.MODE_SPEC] = specs
    held = cook.HeldCook()
    held.hold(cook.PARAM_TEMPERATURE, 356, resources)

    assert held.values == {cook.PARAM_TEMPERATURE: 356}
    with pytest.raises(cook.CookStartError, match="cook_mode_required"):
        held.plan(resources)
    held.hold(cook.PARAM_MODE, "AirFryer", resources)

    assert held.plan(resources).temperature == 360
    assert (
        held.overlay(resources)[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "desired"]
        == "360"
    )


@pytest.mark.parametrize("duration", [-60, 86400])
def test_pre_mode_time_rejected_when_no_available_mode_accepts_it(duration):
    resources = _without_default()
    held = cook.HeldCook()

    with pytest.raises(cook.CookStartError):
        held.hold(cook.PARAM_DURATION, duration, resources)

    assert held.values == {}


def test_one_available_mode_must_accept_the_entire_pending_recipe():
    resources = _without_default()
    resources[cook.MODE_HREF][PREFIX + "supportedModes"] = ["Bake", "AirFryer"]
    specs = json.loads(resources[cook.MODE_HREF][cook.MODE_SPEC])
    for spec in specs:
        if spec["mode"] == "Bake":
            spec["timeMax"] = spec["timeDefault"] = "00:05:00"
    resources[cook.MODE_HREF][cook.MODE_SPEC] = specs
    held = cook.HeldCook()
    held.hold(cook.PARAM_DURATION, 600, resources)

    with pytest.raises(cook.CookStartError):
        held.hold(cook.PARAM_TEMPERATURE, 200, resources)

    assert held.values == {cook.PARAM_DURATION: 600}


def test_reselection_and_start_recheck_changed_live_limits():
    resources = _without_default()
    held = cook.HeldCook()
    held.hold(cook.PARAM_MODE, "Bake", resources)
    held.hold(cook.PARAM_TEMPERATURE, 400, resources)
    specs = json.loads(resources[cook.MODE_HREF][cook.MODE_SPEC])
    for spec in specs:
        if spec["mode"] == "Bake":
            spec["tempMaxF"] = "375"
    resources[cook.MODE_HREF][cook.MODE_SPEC] = specs

    with pytest.raises(cook.CookStartError, match="cook_temperature_out_of_range"):
        held.hold(cook.PARAM_MODE, "Bake", resources)
    with pytest.raises(cook.CookStartError, match="cook_temperature_out_of_range"):
        held.plan(resources)

    assert held.values == {cook.PARAM_MODE: "Bake", cook.PARAM_TEMPERATURE: 400}


@pytest.mark.parametrize("default", [None, "Bake"])
def test_reselecting_the_first_explicit_mode_keeps_values_with_or_without_default(default):
    resources = _without_default()
    if default:
        resources[cook.MODE_HREF][PREFIX + "defaultMode"] = default
    held = cook.HeldCook()
    held.hold(cook.PARAM_TEMPERATURE, 400, resources)
    held.hold(cook.PARAM_DURATION, 600, resources)
    for _ in range(2):
        held.hold(cook.PARAM_MODE, "Bake", resources)
        assert held.plan(resources) == cook.CookPlan("Bake", 400, "Fahrenheit", 600)

    held.hold(cook.PARAM_MODE, "AirFryer", resources)

    assert held.values == {cook.PARAM_MODE: "AirFryer"}
    assert held.plan(resources) == cook.plan_start(resources, mode="AirFryer")


@pytest.mark.parametrize(
    "parameter,value,target,seconds",
    [
        (cook.PARAM_MODE, "Bake", "425", "00:50:00"),
        (cook.PARAM_TEMPERATURE, 400, "400", "00:50:00"),
        (cook.PARAM_DURATION, 0, "425", "00:00:00"),
    ],
)
async def test_pending_view_preserves_unset_readings_and_diagnostics(
    hass, parameter, value, target, seconds
):
    coordinator = await _discovered(hass, _routes(divided=False))
    resources = deepcopy(coordinator._cavity_resources(MAIN))
    resources[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "desired"] = "425"
    probe = {PREFIX + "id": "1", PREFIX + "desired": "150", PREFIX + "current": "75"}
    resources[cook.TEMPERATURES_HREF][PREFIX + "items"].append(probe)
    resources[cook.OPERATION_HREF][PREFIX + "operationTime"] = "00:50:00"
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    before = deepcopy(coordinator.last_resources)
    bound = next(
        b
        for b in coordinator.bound
        if b.subdevice == MAIN and getattr(b.desc, "cook_param", None) == parameter
    )
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound, value)

    view = coordinator.entity_resources()
    assert view[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "desired"] == target
    assert view[cook.TEMPERATURES_HREF][PREFIX + "items"][1] == probe
    assert view[cook.OPERATION_HREF][PREFIX + "operationTime"] == seconds
    assert coordinator.data["oven_setpoint"] == (int(target) if target else None)
    expected_seconds = cook.parse_hms(seconds)
    assert expected_seconds is not None
    assert coordinator.data["cook_time"] == expected_seconds / 60
    assert coordinator.last_resources == before
    assert not _requests()
    if parameter == cook.PARAM_DURATION:
        held = coordinator._held_cooks[MAIN]
        held.hold(cook.PARAM_MODE, "Bake", resources)
        held.hold(cook.PARAM_TEMPERATURE, 400, resources)
        assert held.plan(resources).duration == 0


def test_a_native_unit_change_discards_pending_values():
    resources = _without_default()
    held = cook.HeldCook()
    held.hold(cook.PARAM_TEMPERATURE, 200, resources)
    changed = deepcopy(resources)
    changed[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "unit"] = "Celsius"

    assert held.overlay(changed) is changed
    assert held.values == {}


@pytest.mark.parametrize("has_default", [True, False])
@pytest.mark.parametrize(
    "order", list(permutations((cook.PARAM_MODE, cook.PARAM_TEMPERATURE, cook.PARAM_DURATION)))
)
def test_microwave_convection_preserves_entry_order_and_defaults(has_default, order):
    resources = _idle("microwave_mw7300b")
    if not has_default:
        resources[cook.MODE_HREF].pop(PREFIX + "defaultMode")
    held = cook.HeldCook()
    values = {
        cook.PARAM_MODE: "Convection",
        cook.PARAM_TEMPERATURE: 160,
        cook.PARAM_DURATION: 600,
    }
    for parameter in order:
        held.hold(parameter, values[parameter], resources)
    held.hold(cook.PARAM_MODE, "Convection", resources)

    assert held.plan(resources) == cook.CookPlan("Convection", 160, "Celsius", 600)


def test_a_usable_default_is_not_bypassed_by_another_modes_limits():
    resources = _without_default()
    resources[cook.MODE_HREF][PREFIX + "defaultMode"] = "AirFryer"
    held = cook.HeldCook()

    with pytest.raises(cook.CookStartError, match="cook_temperature_out_of_range"):
        held.hold(cook.PARAM_TEMPERATURE, 200, resources)

    assert held.values == {}


def test_time_first_can_choose_a_mode_without_temperature_control():
    resources = _idle("microwave_mw7300b")
    resources[cook.MODE_HREF].pop(PREFIX + "defaultMode")
    held = cook.HeldCook()
    held.hold(cook.PARAM_DURATION, 600, resources)
    held.hold(cook.PARAM_MODE, "Grill", resources)
    held.hold(cook.PARAM_MODE, "Grill", resources)

    assert held.plan(resources) == cook.CookPlan("Grill", None, "Celsius", 600)

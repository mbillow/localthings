"""Pending recipes survive entry order without changing appliance snapshots."""

import json
from copy import deepcopy
from itertools import permutations

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
    hass, index, divided, mode, order
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

    await coordinator.async_start_cooking(cavity)

    assert len(_FakeConnection.log) == 1
    method, path, body, _ = _FakeConnection.log[0]
    assert (method, path) == ("PUT", f"/devices/{index}")
    assert body is not None
    assert body["Device"]["Mode"] == {"modes": [mode]}
    assert body["Device"]["Temperatures"][0]["desired"] == 400
    assert body["Device"]["Operation"] == {"operationTime": "00:20:00", "state": "Run"}
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
        (cook.PARAM_MODE, "Bake", None, None),
        (cook.PARAM_TEMPERATURE, 400, "400", None),
        (cook.PARAM_DURATION, 0, None, "00:00:00"),
    ],
)
async def test_pending_view_hides_previous_cook_values_without_changing_diagnostics(
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
    assert coordinator.data["cook_time"] == (0 if seconds else None)
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

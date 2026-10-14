"""Paired hour/minute controls share one cavity's staged or live timer."""

import asyncio
from itertools import permutations

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.entity import _is_included
from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    LOWER,
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
    _FakeConnection,
    _lower,
    _requests,
    _routes,
)


def _numbers(coordinator, cavity):
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    return bound, {
        key: LocalThingsNumber(coordinator, bound[key])
        for key in ("cook_time", "cook_time_hours", "cook_time_minutes")
    }


@pytest.mark.parametrize(
    "order",
    list(permutations(("oven_mode", "oven_setpoint", "cook_time_hours", "cook_time_minutes"))),
)
@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
async def test_duration_parts_survive_every_setting_order(hass, order, index, divided, mode):
    coordinator = await _discovered(hass, _routes(divided=divided))
    cavity = _lower(coordinator) if index else MAIN
    bound, numbers = _numbers(coordinator, cavity)
    values = {
        "oven_mode": mode,
        "oven_setpoint": 350,
        "cook_time_hours": 1,
        "cook_time_minutes": 30,
    }
    _FakeConnection.log.clear()

    for key in order:
        await coordinator.async_send_command(bound[key], values[key])
    await coordinator.async_send_command(bound["oven_mode"], mode)

    assert [numbers[key].native_value for key in numbers] == [90, 1, 30]
    assert coordinator._held_cooks[cavity].plan(coordinator._cavity_resources(cavity)) == (
        cook.CookPlan(mode, 350, "Fahrenheit", 5400)
    )
    assert coordinator._held_cooks.get(_lower(coordinator) if not index else MAIN) is None
    assert not _requests()


@pytest.mark.parametrize("index", [0, 1])
async def test_parts_start_as_one_duration_on_the_selected_cavity(hass, index):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    bound, numbers = _numbers(coordinator, cavity)
    await coordinator.async_send_command(
        bound["oven_mode"], "LowerBake" if index else "UpperConvectionBake"
    )
    await coordinator.async_send_command(bound["oven_setpoint"], 350)
    await numbers["cook_time_hours"].async_set_native_value(1)
    await numbers["cook_time_minutes"].async_set_native_value(30)
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["start_cooking"], "")

    writes = [(path, body) for method, path, body, _ in _FakeConnection.log if method == "PUT"]
    assert len(writes) == 2
    assert [path for path, _ in writes] == [f"/devices/{index}"] * 2
    settings = writes[0][1]
    assert settings is not None
    assert settings["Device"]["Operation"] == {"operationTime": "01:30:00"}
    assert writes[1][1] == {"Device": {"Operation": {"state": "Run"}}}


async def test_parts_keep_blank_zero_limits_and_total_duration_compatibility(hass):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator)
    bound, numbers = _numbers(coordinator, cavity)
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    assert all(number.native_value is None for number in numbers.values())
    for key, maximum, unit in (("cook_time_hours", 9, "h"), ("cook_time_minutes", 59, "min")):
        number = numbers[key]
        number.hass = hass
        assert _is_included(bound[key], coordinator)
        assert number.capability_attributes == {"mode": "box", "min": 0, "max": maximum, "step": 1}
        assert number.device_class is None
        assert number.native_unit_of_measurement == unit

    await numbers["cook_time"].async_set_native_value(599)
    assert [number.native_value for number in numbers.values()] == [599, 9, 59]
    await numbers["cook_time_hours"].async_set_native_value(0)
    assert [number.native_value for number in numbers.values()] == [59, 0, 59]
    await numbers["cook_time_minutes"].async_set_native_value(0)
    assert [number.native_value for number in numbers.values()] == [0, 0, 0]


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("cook_time_hours", -1, "cook_duration_part_invalid"),
        ("cook_time_hours", 1.5, "cook_duration_part_invalid"),
        ("cook_time_hours", 10, "cook_duration_total_out_of_range"),
        ("cook_time_minutes", 60, "cook_duration_part_invalid"),
        ("cook_time_minutes", float("inf"), "cook_duration_part_invalid"),
        ("cook_time_minutes", float("nan"), "cook_duration_part_invalid"),
    ],
)
async def test_invalid_parts_preserve_pending_duration_without_writes(hass, key, value, error):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    await numbers["cook_time"].async_set_native_value(90)
    _FakeConnection.log.clear()
    with pytest.raises(ServiceValidationError) as err:
        await numbers[key].async_set_native_value(value)
    assert err.value.translation_key == error
    assert [number.native_value for number in numbers.values()] == [90, 1, 30]
    assert not _requests()


async def test_concurrent_parts_keep_both_edits(hass):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    await asyncio.gather(
        numbers["cook_time_hours"].async_set_native_value(1),
        numbers["cook_time_minutes"].async_set_native_value(30),
    )
    assert [number.native_value for number in numbers.values()] == [90, 1, 30]


async def test_running_part_write_preserves_other_component_and_cavity(hass):
    lower = {
        **LOWER,
        "Mode": {**LOWER["Mode"], "modes": ["LowerBake"]},
        "Operation": {**LOWER["Operation"], "state": "Run", "operationTime": "01:30:00"},
    }
    coordinator = await _discovered(hass, _routes(lower=lower))
    _, numbers = _numbers(coordinator, _lower(coordinator))
    _FakeConnection.log.clear()
    await numbers["cook_time_minutes"].async_set_native_value(45)
    writes = [(path, body) for method, path, body, _ in _FakeConnection.log if method == "PUT"]
    assert writes == [
        (
            "/devices/1",
            {"Device": {"Operation": {"operationTime": "01:45:00", "remainingTime": "01:45:00"}}},
        )
    ]
    assert not coordinator._held_cooks


async def test_missing_duration_does_not_guess_the_other_component(hass):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    coordinator._observe.apply(
        "/operational/state/vs/1", {PREFIX + "operationTime": "NotSupported"}, source="poll"
    )
    _FakeConnection.log.clear()
    with pytest.raises(ServiceValidationError) as err:
        await numbers["cook_time_hours"].async_set_native_value(1)
    assert err.value.translation_key == "cook_duration_unavailable"
    assert not _requests()


async def test_other_boards_do_not_gain_duration_parts(hass):
    coordinator = await _discovered(hass)
    bound, _ = _numbers(coordinator, MAIN)
    coordinator._observe.apply(
        "/information/vs/0", {PREFIX + "modelNum": "OTHER_OVEN"}, source="poll"
    )
    assert _is_included(bound["cook_time"], coordinator)
    assert not _is_included(bound["cook_time_hours"], coordinator)
    assert not _is_included(bound["cook_time_minutes"], coordinator)

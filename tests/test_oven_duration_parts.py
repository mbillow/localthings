"""Paired hour/minute controls share one cavity's staged or live timer."""

import asyncio
from itertools import permutations

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_cook_times_superseded_by_parts
from custom_components.localthings.const import DOMAIN
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
        for key in ("cook_time_hours", "cook_time_minutes")
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

    assert [numbers[key].native_value for key in numbers] == [1, 30]
    assert coordinator._held_cooks[cavity].plan(coordinator._cavity_resources(cavity)) == (
        cook.CookPlan(mode, 350, "Fahrenheit", 5400)
    )
    assert coordinator._held_cooks.get(_lower(coordinator) if not index else MAIN) is None
    assert not _requests()


@pytest.mark.parametrize("state", ["Ready", "Run"])
async def test_narrower_mode_limit_rejects_combined_duration_before_writing(hass, state):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, MAIN)
    coordinator._observe.apply(
        "/mode/vs/0",
        {
            PREFIX + "modes": ["UpperConvectionBake"],
            cook.MODE_SPEC: [
                {"mode": "UpperConvectionBake", "control": "Start&Setting", "timeMax": "02:00:00"}
            ],
        },
        source="poll",
    )
    coordinator._observe.apply(
        cook.OPERATION_HREF,
        {
            PREFIX + "state": state,
            PREFIX + "operationTime": "01:30:00",
        },
        source="poll",
    )
    assert numbers["cook_time_hours"].native_max_value == 2
    _FakeConnection.log.clear()
    with pytest.raises(ServiceValidationError) as err:
        await numbers["cook_time_hours"].async_set_native_value(2)
    assert err.value.translation_key == "cook_duration_total_out_of_range"
    assert err.value.translation_placeholders == {"max": "02:00:00"}
    assert coordinator.resource(cook.OPERATION_HREF)[PREFIX + "operationTime"] == "01:30:00"
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


async def test_parts_replace_total_duration_and_keep_blank_zero_and_limits(hass):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator)
    bound, numbers = _numbers(coordinator, cavity)
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    assert all(number.native_value is None for number in numbers.values())
    assert not _is_included(bound["cook_time"], coordinator)
    assert f"{cavity.key_prefix}cook_time" not in coordinator.data
    for key, maximum, unit in (("cook_time_hours", 9, "h"), ("cook_time_minutes", 59, "min")):
        number = numbers[key]
        number.hass = hass
        assert _is_included(bound[key], coordinator)
        assert number.capability_attributes == {"mode": "box", "min": 0, "max": maximum, "step": 1}
        assert number.device_class is None
        assert number.native_unit_of_measurement == unit

    await numbers["cook_time_hours"].async_set_native_value(9)
    await numbers["cook_time_minutes"].async_set_native_value(59)
    assert [number.native_value for number in numbers.values()] == [9, 59]
    await numbers["cook_time_hours"].async_set_native_value(0)
    assert [number.native_value for number in numbers.values()] == [0, 59]
    await numbers["cook_time_minutes"].async_set_native_value(0)
    assert [number.native_value for number in numbers.values()] == [0, 0]


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("cook_time_hours", -1, "cook_duration_part_invalid"),
        ("cook_time_hours", 1.5, "cook_duration_part_invalid"),
        ("cook_time_hours", 10, "cook_duration_part_invalid"),
        ("cook_time_minutes", 60, "cook_duration_part_invalid"),
        ("cook_time_minutes", float("inf"), "cook_duration_part_invalid"),
        ("cook_time_minutes", float("nan"), "cook_duration_part_invalid"),
    ],
)
async def test_invalid_parts_preserve_pending_duration_without_writes(hass, key, value, error):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    await numbers["cook_time_hours"].async_set_native_value(1)
    await numbers["cook_time_minutes"].async_set_native_value(30)
    _FakeConnection.log.clear()
    with pytest.raises(ServiceValidationError) as err:
        await numbers[key].async_set_native_value(value)
    assert err.value.translation_key == error
    assert [number.native_value for number in numbers.values()] == [1, 30]
    assert not _requests()


async def test_concurrent_parts_keep_both_edits(hass):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    await asyncio.gather(
        numbers["cook_time_hours"].async_set_native_value(1),
        numbers["cook_time_minutes"].async_set_native_value(30),
    )
    assert [number.native_value for number in numbers.values()] == [1, 30]


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


@pytest.mark.parametrize(
    "board", ["LCD_OV_WALL", "LCD_OV_WALL_16K", "LCD_OV_WALL_20K", "LCD_OV_WALL_30K"]
)
async def test_wall_family_gets_paired_controls_for_both_cavities(hass, board):
    """Family routing coverage; the sibling board names are not hardware captures."""
    coordinator = await _discovered(hass)
    for index, cavity in enumerate((MAIN, _lower(coordinator))):
        coordinator._observe.apply(
            f"/information/vs/{index}", {PREFIX + "modelNum": board + "|1|2"}, source="poll"
        )
        bound, numbers = _numbers(coordinator, cavity)
        assert not _is_included(bound["cook_time"], coordinator)
        assert all(_is_included(number._bound, coordinator) for number in numbers.values())
        assert numbers["cook_time_hours"].native_max_value == 9
        assert numbers["cook_time_minutes"].native_max_value == 59


@pytest.mark.parametrize(
    "board", ["LCD_OV_WALLPAPER_16K", "TP2X_KS_WALLOVEN", "LCD_R18_OV_QMD_EU_22K"]
)
async def test_other_families_keep_the_original_total_duration(hass, board):
    coordinator = await _discovered(hass)
    coordinator._observe.apply("/information/vs/0", {PREFIX + "modelNum": board}, source="poll")
    bound, numbers = _numbers(coordinator, MAIN)
    assert _is_included(bound["cook_time"], coordinator)
    assert not any(_is_included(number._bound, coordinator) for number in numbers.values())


async def test_total_duration_cleanup_removes_only_replaced_numbers(hass):
    coordinator = await _discovered(hass)
    registry = er.async_get(hass)
    removed, kept = [], []
    for cavity in (MAIN, _lower(coordinator)):
        bound, numbers = _numbers(coordinator, cavity)
        total = LocalThingsNumber(coordinator, bound["cook_time"])
        assert total.unique_id is not None
        old = registry.async_get_or_create(
            "number", DOMAIN, total.unique_id, config_entry=coordinator._entry
        )
        removed.append(
            registry.async_update_entity(
                old.entity_id, new_entity_id=f"number.renamed_timer_{len(removed)}"
            )
        )
        for number in numbers.values():
            assert number.unique_id is not None
            kept.append(
                registry.async_get_or_create(
                    "number", DOMAIN, number.unique_id, config_entry=coordinator._entry
                )
            )
        kept.append(
            registry.async_get_or_create(
                "sensor", DOMAIN, total.unique_id, config_entry=coordinator._entry
            )
        )
    kept.append(
        registry.async_get_or_create(
            "number", DOMAIN, "UNRELATED_cook_time", config_entry=coordinator._entry
        )
    )

    for _ in range(2):
        _drop_cook_times_superseded_by_parts(hass, coordinator._entry, coordinator)
        assert all(registry.async_get(item.entity_id) is None for item in removed)
        assert all(registry.async_get(item.entity_id) is not None for item in kept)


@pytest.mark.parametrize("case", ["other_model", "missing_minutes", "other_entry"])
async def test_total_duration_cleanup_requires_both_parts_and_owning_entry(hass, case):
    coordinator = await _discovered(hass)
    bound, _ = _numbers(coordinator, MAIN)
    if case == "other_model":
        coordinator._observe.apply(
            "/information/vs/0", {PREFIX + "modelNum": "OTHER_OVEN"}, source="poll"
        )
    elif case == "missing_minutes":
        coordinator.bound = [b for b in coordinator.bound if b.desc.key != "cook_time_minutes"]
    entry = coordinator._entry
    if case == "other_entry":
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
    total = LocalThingsNumber(coordinator, bound["cook_time"])
    assert total.unique_id is not None
    registry = er.async_get(hass)
    old = registry.async_get_or_create("number", DOMAIN, total.unique_id, config_entry=entry)

    _drop_cook_times_superseded_by_parts(hass, coordinator._entry, coordinator)

    assert registry.async_get(old.entity_id) is not None


@pytest.mark.parametrize("state", ["Ready", "Run"])
async def test_component_caps_hold_with_a_larger_live_mode_spec(hass, state):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, MAIN)
    coordinator._observe.apply(
        "/mode/vs/0",
        {
            PREFIX + "modes": ["UpperConvectionBake"],
            cook.MODE_SPEC: [
                {"mode": "UpperConvectionBake", "control": "Start&Setting", "timeMax": "20:00:00"}
            ],
        },
        source="poll",
    )
    coordinator._observe.apply(cook.OPERATION_HREF, {PREFIX + "state": state}, source="poll")
    assert numbers["cook_time_hours"].native_max_value == 9
    assert numbers["cook_time_minutes"].native_max_value == 59
    _FakeConnection.log.clear()
    for key, value in (("cook_time_hours", 10), ("cook_time_minutes", 60)):
        with pytest.raises(ServiceValidationError) as err:
            await numbers[key].async_set_native_value(value)
        assert err.value.translation_key == "cook_duration_part_invalid"
    assert not _requests()

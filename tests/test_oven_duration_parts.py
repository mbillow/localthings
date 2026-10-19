"""NV51K7770S duration components retain one timer per cavity."""

import asyncio
import json
from pathlib import Path
from string import Formatter

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
    PREFIX,
    UPPER,
    _discovered,
    _fake_https,  # noqa: F401
    _FakeConnection,
    _lower,
    _requests,
    _routes,
)


async def _prepared(hass):
    coordinator = await _discovered(hass)
    for cavity, mode in ((MAIN, "UpperConvectionBake"), (_lower(coordinator), "LowerBake")):
        bound, _ = _numbers(coordinator, cavity)
        await coordinator.async_send_command(bound["oven_mode"], mode)
    return coordinator


def _numbers(coordinator, cavity):
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    return bound, {
        key: LocalThingsNumber(coordinator, bound[key])
        for key in ("cook_time_hours", "cook_time_minutes")
    }


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


async def test_parts_replace_total_duration_and_keep_blank_zero_and_limits(hass):
    coordinator = await _prepared(hass)
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
    coordinator = await _prepared(hass)
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
    coordinator = await _prepared(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    await asyncio.gather(
        numbers["cook_time_hours"].async_set_native_value(1),
        numbers["cook_time_minutes"].async_set_native_value(30),
    )
    assert [number.native_value for number in numbers.values()] == [1, 30]


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


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
@pytest.mark.parametrize("hours_first", [True, False])
async def test_parts_feed_one_start_plan_without_writes(hass, index, divided, mode, hours_first):
    upper = (
        UPPER
        if divided
        else {**UPPER, "Mode": {**UPPER["Mode"], "supportedModes": ["NoOperation", "Bake"]}}
    )
    coordinator = await _discovered(hass, _routes(divided=divided, upper=upper))
    cavity = _lower(coordinator) if index else MAIN
    bound, numbers = _numbers(coordinator, cavity)
    await coordinator.async_send_command(bound["oven_mode"], mode)
    await coordinator.async_send_command(bound["oven_setpoint"], 350)
    _FakeConnection.log.clear()
    edits = [("cook_time_hours", 1), ("cook_time_minutes", 30)]
    for key, value in edits if hours_first else reversed(edits):
        await numbers[key].async_set_native_value(value)
    assert [number.native_value for number in numbers.values()] == [1, 30]
    plan = coordinator._held_cooks[cavity].plan(coordinator._cavity_resources(cavity))
    assert plan == cook.CookPlan(mode, 350, "Fahrenheit", 5400)
    operation = next(
        item
        for item in plan.batch(cavity.to_actual)
        if item.get("href") == f"/operational/state/vs/{index}"
    )
    assert operation["rep"][PREFIX + "operationTime"] == "01:30:00"
    assert not _requests()
    assert not any(
        held.values for other, held in coordinator._held_cooks.items() if other != cavity
    )


@pytest.mark.parametrize(
    "board",
    [
        "LCD_OV_WALL",
        "LCD_OV_WALL_20K",
        "LCD_OV_WALL_30K",
        "LCD_OV_WALLPAPER_16K",
        "TP2X_KS_WALLOVEN",
        "LCD_R18_OV_QMD_EU_22K",
    ],
)
async def test_other_models_keep_the_original_slider(hass, board):
    coordinator = await _discovered(hass)
    for index, cavity in enumerate((MAIN, _lower(coordinator))):
        coordinator._observe.apply(
            f"/information/vs/{index}", {PREFIX + "modelNum": board + "|1|2"}, source="poll"
        )
        bound, numbers = _numbers(coordinator, cavity)
        total = LocalThingsNumber(coordinator, bound["cook_time"])
        assert _is_included(bound["cook_time"], coordinator)
        assert total.mode == "slider"
        assert total.native_unit_of_measurement == "min"
        assert not any(_is_included(number._bound, coordinator) for number in numbers.values())


@pytest.mark.parametrize(
    "value,expected",
    [
        ("01:30:00", [1, 30]),
        ("00:00:00", [0, 0]),
        ("01:59:01", [2, 0]),
        ("NotSupported", [None, None]),
    ],
)
async def test_live_parts_use_operation_time_not_remaining_time(hass, value, expected):
    coordinator = await _discovered(hass)
    _, numbers = _numbers(coordinator, _lower(coordinator))
    coordinator._observe.apply(
        "/operational/state/vs/1",
        {
            PREFIX + "state": "Run",
            PREFIX + "operationTime": value,
            PREFIX + "remainingTime": "00:00:17",
        },
        source="poll",
    )
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    assert [number.native_value for number in numbers.values()] == expected


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "sk"])
def test_duration_translations_and_error_placeholders(language):
    catalogs = Path(__file__).parents[1] / "custom_components/localthings/translations"
    data = json.loads((catalogs / f"{language}.json").read_text())
    for key in ("cook_time_hours", "cook_time_minutes"):
        assert data["entity"]["number"][key]["name"].strip()
    for key, expected in (
        ("cook_duration_part_invalid", set()),
        ("cook_duration_unavailable", set()),
        ("cook_duration_total_out_of_range", {"max"}),
    ):
        message = data["exceptions"][key]["message"]
        assert message.strip()
        assert {field for _, field, _, _ in Formatter().parse(message) if field} == expected

"""NV51K7770S recipe labels and limits, without changing other oven boards."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.capabilities.oven import _oven_setpoint_write
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.select import LocalThingsSelect
from tests.test_legacy_http_lower_cavity import (
    FIXTURE,
    LOWER,
    PREFIX,
    UPPER,
    _discovered,
    _fake_https,  # noqa: F401 -- installs the fake legacy transport
    _FakeConnection,
    _lower,
    _requests,
    _routes,
    _seed,
    _supported,
    _transport,
)


@pytest.mark.parametrize("index,prefix", [(0, "Upper"), (1, "Lower"), (0, "")])
@pytest.mark.parametrize("recipe,preset", [(1, 375), (2, 375), (3, 375), (4, 400)])
@pytest.mark.parametrize("unit", ["Fahrenheit", "Celsius"])
async def test_recipe_selection_keeps_raw_code_preset_and_cavity(
    hass, index, prefix, recipe, preset, unit
):
    upper, lower = deepcopy(UPPER), deepcopy(LOWER)
    for cavity_data in (upper, lower):
        cavity_data["Temperatures"][0]["unit"] = unit
    coordinator = await _discovered(hass, _routes(divided=bool(prefix), upper=upper, lower=lower))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    select = LocalThingsSelect(coordinator, bound["oven_mode"])
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    assert select.translation_key == "oven_mode_lcd_wall_16k"
    _FakeConnection.log.clear()

    option = f"{prefix.lower()}_healthycook{recipe}" if prefix else f"healthycook{recipe}"
    await select.async_select_option(option)
    await coordinator.async_send_command(bound["cook_time"], 5)

    maximum = 480 if prefix else 550
    minimum = 175
    if unit == "Celsius":
        minimum, maximum = 80, 250 if prefix else 285
        preset = 205 if recipe == 4 else 190
    assert select.current_option == option
    assert target.native_value == preset
    assert (target.native_min_value, target.native_max_value, target.native_step) == (
        minimum,
        maximum,
        5,
    )
    plan = coordinator._held_cooks[cavity].plan(coordinator._cavity_resources(cavity))
    assert (plan.mode, plan.temperature, plan.duration) == (
        f"{prefix}Healthycook{recipe}",
        preset,
        300,
    )
    assert plan.unit == unit
    elements = plan.batch(cavity.to_actual)
    assert {e["href"] for e in elements} <= {
        cavity.to_actual(href)
        for href in (cook.MODE_HREF, cook.TEMPERATURES_HREF, cook.OPERATION_HREF)
    }
    assert not any(
        held.values for other, held in coordinator._held_cooks.items() if other != cavity
    )
    with pytest.raises(ServiceValidationError) as err:
        await target.async_set_native_value(maximum + 5)
    assert err.value.translation_key == "cook_temperature_out_of_range"
    assert not _requests()


@pytest.mark.parametrize("recipe,preset,maximum,step", [(5, 150, 225, 5), (6, 100, 100, 1)])
async def test_special_recipe_presets_and_bounds(hass, recipe, preset, maximum, step):
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["oven_mode"], f"Healthycook{recipe}")

    assert target.native_value == preset
    assert (target.native_min_value, target.native_max_value, target.native_step) == (
        100,
        maximum,
        step,
    )
    assert target.available
    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.mode, plan.temperature) == (f"Healthycook{recipe}", preset)
    with pytest.raises(ServiceValidationError) as err:
        await target.async_set_native_value(maximum + 5)
    assert err.value.translation_key == "cook_temperature_out_of_range"
    assert target.native_value == preset
    assert not _requests()


@pytest.mark.parametrize("recipe,preset,maximum", [(5, 150, 225), (6, 100, 100)])
async def test_panel_started_special_recipes_reject_out_of_range_write(
    hass, recipe, preset, maximum
):
    upper = {
        **UPPER,
        "Mode": {**UPPER["Mode"], "modes": [f"Healthycook{recipe}"]},
        "Operation": {**UPPER["Operation"], "state": "Run"},
        "Oven": {**UPPER["Oven"], "state": "Cooking"},
        "Temperatures": [{**UPPER["Temperatures"][0], "desired": preset}],
    }
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    bound = next(
        b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "oven_setpoint"
    )
    target = LocalThingsNumber(coordinator, bound)
    assert (target.native_value, target.native_min_value, target.native_max_value) == (
        preset,
        100,
        maximum,
    )
    _FakeConnection.log.clear()

    await target.async_set_native_value(maximum + 5)

    assert not _requests()
    assert target.native_value == preset


@pytest.mark.parametrize("prefix,maximum", [("Upper", 250), ("Lower", 250), ("", 285)])
@pytest.mark.parametrize("recipe", [1, 2, 3, 4])
async def test_native_celsius_has_manual_limits_and_panel_presets(hass, prefix, maximum, recipe):
    coordinator = await _discovered(hass, _routes(divided=bool(prefix)))
    cavity = _lower(coordinator) if prefix == "Lower" else MAIN
    resources = deepcopy(coordinator._cavity_resources(cavity))
    resources[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "unit"] = "Celsius"
    mode = f"{prefix}Healthycook{recipe}"
    assert cook.temp_bounds(resources, mode) == (80, maximum, 5)
    plan = cook.plan_start(resources, mode=mode)
    assert (plan.temperature, plan.unit) == (205 if recipe == 4 else 190, "Celsius")
    plan = cook.plan_start(resources, mode=mode, temperature=maximum)
    assert (plan.temperature, plan.unit) == (maximum, "Celsius")
    with pytest.raises(cook.CookStartError):
        cook.plan_start(resources, mode=mode, temperature=maximum + 5)


async def test_native_celsius_proof_is_fixed_and_dehydrate_is_adjustable(hass):
    upper = {**UPPER, "Temperatures": [{**UPPER["Temperatures"][0], "unit": "Celsius"}]}
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["oven_mode"], "Healthycook6")

    assert target.available
    assert (target.native_value, target.native_min_value, target.native_max_value) == (40, 40, 40)
    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.temperature, plan.unit) == (40, "Celsius")
    with pytest.raises(ServiceValidationError) as err:
        await target.async_set_native_value(45)
    assert err.value.translation_key == "cook_temperature_out_of_range"
    await coordinator.async_send_command(bound["oven_mode"], "Healthycook5")
    assert target.available
    assert (target.native_min_value, target.native_max_value) == (40, 105)
    assert target.native_value == 65
    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.temperature, plan.unit) == (65, "Celsius")
    await target.async_set_native_value(40)
    assert target.native_value == 40
    assert not _requests()
    coordinator.last_update_success = False
    assert not target.available


async def test_fahrenheit_oven_uses_ha_celsius_display_and_input_conversion(hass):
    hass.config.units = METRIC_SYSTEM
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    target.hass = hass

    await coordinator.async_send_command(bound["oven_mode"], "Healthycook6")

    assert target.available
    assert target.native_unit_of_measurement == "°F"
    assert target.unit_of_measurement == "°C"
    assert target.value == 38
    await coordinator.async_send_command(bound["oven_mode"], "Healthycook1")
    await target.async_set_native_value(target.convert_to_native_value(200))
    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.temperature, plan.unit) == (390, "Fahrenheit")


@pytest.mark.parametrize(
    "mode,unit,minimum,maximum",
    [
        ("UpperHealthycook1", "Fahrenheit", 175, 480),
        ("LowerHealthycook4", "Fahrenheit", 175, 480),
        ("Healthycook4", "Fahrenheit", 175, 550),
        ("Healthycook5", "Fahrenheit", 100, 225),
        ("Healthycook6", "Fahrenheit", 100, 100),
        ("Healthycook5", "Celsius", 40, 105),
        ("Healthycook6", "Celsius", 40, 40),
    ],
)
async def test_running_setpoint_bounds_preserve_probe_item(hass, mode, unit, minimum, maximum):
    coordinator = await _discovered(hass, _routes(divided=False))
    resources = deepcopy(coordinator._cavity_resources(MAIN))
    resources[cook.MODE_HREF][PREFIX + "modes"] = [mode]
    resources[cook.MODE_HREF][PREFIX + "supportedModes"] = [mode]
    rep = resources[cook.TEMPERATURES_HREF]
    rep[PREFIX + "items"][0][PREFIX + "unit"] = unit
    probe = {PREFIX + "id": "1", PREFIX + "desired": "150", PREFIX + "current": "80"}
    rep[PREFIX + "items"].append(probe)
    before = deepcopy(rep)

    result = _oven_setpoint_write(minimum, rep, resources=resources)

    assert result is not None
    path, body = result
    assert path == ["temperatures", "vs", "0"]
    assert body[PREFIX + "items"][0][PREFIX + "desired"] == str(minimum)
    assert body[PREFIX + "items"][1] == probe
    assert rep == before
    assert _oven_setpoint_write(maximum + 5, rep, resources=resources) is None


async def test_live_mode_spec_overrides_fallback_including_native_celsius_proof(hass):
    coordinator = await _discovered(hass, _routes(divided=False))
    resources = deepcopy(coordinator._cavity_resources(MAIN))
    resources[cook.TEMPERATURES_HREF][PREFIX + "items"][0][PREFIX + "unit"] = "Celsius"
    resources[cook.MODE_HREF][cook.MODE_SPEC] = [
        {
            "mode": "Healthycook6",
            "control": "Start&Setting",
            "tempMinC": "38",
            "tempMaxC": "38",
            "tempDefaultC": "38",
            "tempIntervalC": "5",
        }
    ]
    assert cook.temp_bounds(resources, "Healthycook6") == (38, 38, 5)
    assert cook.plan_start(resources, mode="Healthycook6", duration=60).temperature == 38
    resources[cook.MODE_HREF][PREFIX + "modes"] = ["Healthycook6"]
    bound = next(
        b for b in coordinator.bound if b.desc.key == "oven_setpoint" and b.subdevice == MAIN
    )
    assert bound.desc.available_fn(resources[cook.TEMPERATURES_HREF], resources)
    assert _oven_setpoint_write(38, resources[cook.TEMPERATURES_HREF], resources=resources)


async def test_live_proof_without_a_temperature_disables_the_setpoint(hass):
    upper = deepcopy(UPPER)
    upper["Mode"].update(
        modes=["Healthycook6"],
        modeSpec=[{"mode": "Healthycook6", "control": "Start&Setting"}],
    )
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    bound = next(b for b in coordinator.bound if b.desc.key == "oven_setpoint")
    target = LocalThingsNumber(coordinator, bound)
    resources = coordinator._cavity_resources(MAIN)

    assert not target.available
    assert _oven_setpoint_write(100, resources[cook.TEMPERATURES_HREF], resources=resources) is None


@pytest.mark.parametrize("subtype", ["CombiOven", None])
async def test_same_board_without_wall_oven_identity_keeps_generic_behavior(hass, subtype):
    routes = _routes()
    for index in (0, 1):
        info = deepcopy(FIXTURE[f"device{index}_information"])
        info["Information"]["deviceSubType"] = subtype
        routes[f"/devices/{index}/information"] = (200, info)
    coordinator = await _discovered(hass, routes)
    for cavity in (MAIN, _lower(coordinator)):
        resources = coordinator._cavity_resources(cavity)
        bound = next(
            b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "oven_mode"
        )
        assert LocalThingsSelect(coordinator, bound).translation_key == "oven_mode"
        assert cook.healthy_cook_temps(resources, "UpperHealthycook1") is None
    lower_modes = coordinator._cavity_resources(_lower(coordinator))[cook.MODE_HREF][
        PREFIX + "supportedModes"
    ]
    assert lower_modes == [
        "NoOperation",
        "LowerBake",
        "LowerConvectionBake",
        "LowerConvectionRoast",
    ]


def test_lower_mode_poll_uses_cached_identity_and_preserves_own_reported_modes():
    transport = _transport()
    _seed(transport, 1)
    _FakeConnection.routes["/devices/1/mode"] = (200, {"Mode": LOWER["Mode"]})

    code, rep = transport.read(["mode", "vs", "1"], timeout=10.0)

    assert code == 0x45
    assert "LowerHealthycook1" in rep[PREFIX + "supportedModes"]
    _FakeConnection.routes["/devices/1/mode"] = (
        200,
        {"Mode": {"supportedModes": ["NoOperation,LowerBake"], "modes": ["NoOperation"]}},
    )
    code, rep = transport.read(["mode", "vs", "1"], timeout=10.0)
    assert code == 0x45
    assert rep[PREFIX + "supportedModes"] == ["NoOperation", "LowerBake"]


@pytest.mark.parametrize("model", ["LCD_OV_WALL_20K", "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", None])
async def test_other_boards_keep_generic_labels_limits_and_lower_modes(hass, model):
    coordinator = await _discovered(hass, _routes(divided=False))
    coordinator._observe.apply("/information/vs/0", {PREFIX + "modelNum": model}, source="poll")
    resources = coordinator._cavity_resources(MAIN)
    assert cook.temp_bounds(resources, "Healthycook5") == (175, 550, 5)
    assert cook.mode_specs(resources)["Healthycook1"].temps["F"].default is None
    bound = next(b for b in coordinator.bound if b.desc.key == "oven_mode" and b.subdevice == MAIN)
    assert LocalThingsSelect(coordinator, bound).translation_key == "oven_mode"
    if model == "LCD_OV_WALL_20K":
        _FakeConnection.routes = _routes()
        info = deepcopy(FIXTURE["device1_information"])
        info["Information"]["modelID"] = model
        _FakeConnection.routes["/devices/1/information"] = (200, info)
        assert _supported(_seed(_transport(model), 1), 1) == [
            "NoOperation",
            "LowerBake",
            "LowerConvectionBake",
            "LowerConvectionRoast",
        ]


@pytest.mark.parametrize(
    "language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "pt-BR", "sk"]
)
def test_recipe_translations_cover_all_cavities_and_preserve_other_labels(language):
    path = (
        Path(__file__).parent.parent
        / "custom_components/localthings/translations"
        / f"{language}.json"
    )
    selects = json.loads(path.read_text())["entity"]["select"]
    generic = selects["oven_mode"]["state"]
    specific = selects["oven_mode_lcd_wall_16k"]["state"]
    assert selects["oven_mode_lcd_wall_16k"]["name"] == selects["oven_mode"]["name"]
    recipe_keys = {f"healthycook{i}" for i in range(1, 7)} | {
        f"{cavity}_healthycook{i}" for cavity in ("upper", "lower") for i in range(1, 5)
    }
    assert set(specific) == set(generic) | recipe_keys
    for key, label in generic.items():
        if "healthycook" not in key:
            assert specific[key] == label
    for recipe in range(1, 5):
        assert (
            specific[f"healthycook{recipe}"]
            == specific[f"upper_healthycook{recipe}"]
            == specific[f"lower_healthycook{recipe}"]
        )
        assert f"healthycook{recipe}" not in generic
    assert specific["healthycook5"].casefold().endswith(generic["dehydrate"].casefold())
    assert specific["healthycook6"].casefold().endswith(generic["bread_proof"].casefold())

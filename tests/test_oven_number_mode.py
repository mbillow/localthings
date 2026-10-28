"""Numeric setpoints are limited to the measured NV51K7770S board."""

import pytest
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.localthings.entity import _is_included
from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.entities import NumberDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.conftest import _discover_full, _load_device_full
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
    _lower,
    _requests,
    _routes,
)
from tests.test_legacy_http_nw9300md import _routes as _combo_routes
from tests.test_number_bounds import _number


@pytest.mark.parametrize("divided", [False, True])
async def test_wall_oven_setpoints_use_boxes_with_or_without_divider(hass, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    targets = []
    for bound in coordinator.bound:
        if not isinstance(bound.desc, NumberDesc):
            continue
        number = LocalThingsNumber(coordinator, bound)
        if bound.desc.key == "oven_setpoint":
            assert number.mode == "box"
            assert number.native_unit_of_measurement == "°F"
            assert (number.native_min_value, number.native_max_value, number.native_step) == (
                175,
                550,
                5,
            )
            targets.append(bound)
        elif bound.desc.key == "cook_time":
            assert number.mode == "slider"
    assert {bound.subdevice for bound in targets} == {MAIN, _lower(coordinator)}


@pytest.mark.parametrize(
    "model",
    [None, 16, "", "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", "LCD_OV_WALL_17K", "TP2X_KS_WALLOVEN"],
)
def test_missing_or_similar_boards_keep_sliders(model):
    resources = {
        "/information/vs/0": {
            PREFIX + "modelNum": model,
            PREFIX + "deviceSubType": "WallOven",
        }
    }
    number = _number(oven.OVEN_SETPOINT.entities[0], resources)
    assert number.mode == "slider"


@pytest.mark.parametrize("subtype", [None, "Unknown", "CombiOven"])
def test_same_board_without_wall_oven_subtype_keeps_slider(subtype):
    resources = {
        "/information/vs/0": {
            PREFIX + "modelNum": "LCD_OV_WALL_16K|1|2",
            PREFIX + "deviceSubType": subtype,
        }
    }
    assert _number(oven.OVEN_SETPOINT.entities[0], resources).mode == "slider"


@pytest.mark.parametrize("divided", [False, True])
async def test_nw9300md_cavities_keep_their_setpoint_sliders(hass, divided):
    coordinator = await _discovered(hass, _combo_routes(divided=divided))
    targets = [
        bound
        for bound in coordinator.bound
        if bound.desc.key == "oven_setpoint" and _is_included(bound, coordinator)
    ]
    assert {bound.subdevice.key for bound in targets} == {"2", "3"}
    assert all(LocalThingsNumber(coordinator, bound).mode == "slider" for bound in targets)


@pytest.mark.parametrize(
    "name",
    [
        "oven",
        "oven_nv75n_dual_cook",
        "oven_tp2x_ks_walloven",
        "range_ne6516a",
        "microwave_mw7300b",
        "refrigerator",
        "airconditioner",
        "washer_ww6500",
    ],
)
def test_other_appliances_and_oven_models_keep_their_number_presentation(name):
    resources, oic_res, seeds = _load_device_full(name)
    bound, _, _, _, _ = _discover_full(resources, oic_res, seeds)
    numbers = [b for b in bound if isinstance(b.desc, NumberDesc)]
    assert numbers
    for entity in numbers:
        assert _number(entity.desc, resources).mode == "slider"


async def test_lower_mode_uses_its_own_identity_and_updates_when_info_arrives(hass):
    coordinator = await _discovered(hass)
    bound = next(
        b
        for b in coordinator.bound
        if b.desc.key == "oven_setpoint" and b.subdevice == _lower(coordinator)
    )
    number = LocalThingsNumber(coordinator, bound)
    info = coordinator.resource("/information/vs/1")
    coordinator._observe.apply(
        "/information/vs/1", {PREFIX + "modelNum": "LCD_OV_WALL_17K"}, source="poll"
    )
    assert number.mode == "slider"
    coordinator._observe.apply("/information/vs/1", info, source="poll")
    assert number.mode == "box"


async def test_numeric_box_keeps_ha_unit_conversion_and_mode_limits(hass):
    hass.config.units = METRIC_SYSTEM
    coordinator = await _discovered(hass)
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    target.hass = hass
    await coordinator.async_send_command(bound["oven_mode"], "UpperConvectionBake")
    assert target.unit_of_measurement == "°C"
    assert target.native_unit_of_measurement == "°F"
    assert target.mode == "box"
    before = _requests()
    await target.async_set_native_value(target.convert_to_native_value(200))
    assert target.native_value == 390
    assert _requests() == before

    coordinator._observe.apply(
        "/mode/vs/0",
        {
            **coordinator.resource("/mode/vs/0"),
            PREFIX + "modeSpec": [
                {
                    "mode": "UpperConvectionBake",
                    "control": "Start&Setting",
                    "tempMinF": 200,
                    "tempMaxF": 450,
                    "tempIntervalF": 10,
                }
            ],
        },
        source="poll",
    )
    assert (target.native_min_value, target.native_max_value, target.native_step) == (200, 450, 10)
    assert target.mode == "box"

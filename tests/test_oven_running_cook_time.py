"""Running timer edits use the NV51K7770S's accepted request body."""

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.button import LocalThingsButton
from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.capabilities import cook, oven
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    LOWER,
    PREFIX,
    UPPER,
    _discovered,
    _fake_https,  # noqa: F401 -- installs the fake legacy transport
    _FakeConnection,
    _lower,
    _routes,
)


def _running_routes(index=1, divided=True, mode="LowerBake"):
    base = LOWER if index else UPPER
    running = {
        **base,
        "Mode": {**base["Mode"], "modes": [mode]},
        "Operation": {
            **base["Operation"],
            "state": "Run",
            "operationTime": "00:02:00",
            "remainingTime": "00:01:47",
        },
    }
    return _routes(divided=divided, **{"lower" if index else "upper": running})


def _timer(coordinator, cavity):
    bound = next(
        b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "cook_time"
    )
    return LocalThingsNumber(coordinator, bound)


def _writes():
    return [(path, body) for method, path, body, _ in _FakeConnection.log if method == "PUT"]


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
@pytest.mark.parametrize("minutes,expected", [(3, "00:03:00"), (90, "01:30:00"), (0, "00:00:00")])
async def test_running_timer_updates_only_duration_in_its_own_cavity(
    hass, index, divided, mode, minutes, expected
):
    coordinator = await _discovered(hass, _running_routes(index, divided, mode))
    cavity = _lower(coordinator) if index else MAIN
    other_href = f"/operational/state/vs/{1 - index}"
    other_before = dict(coordinator.resource(other_href) or {})
    _FakeConnection.log.clear()

    await _timer(coordinator, cavity).async_set_native_value(minutes)

    assert _writes() == [
        (f"/devices/{index}", {"Device": {"Operation": {"operationTime": expected}}})
    ]
    actual = coordinator.resource(cavity.to_actual(cook.OPERATION_HREF))
    assert actual[PREFIX + "remainingTime"] == "00:01:47"
    assert actual[PREFIX + "state"] == "Run"
    assert (coordinator.resource(other_href) or {}) == other_before
    assert not coordinator._held_cooks


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
async def test_start_after_running_timer_edit_is_quiet_without_restarting(
    hass, index, divided, mode
):
    coordinator = await _discovered(hass, _running_routes(index, divided, mode))
    cavity = _lower(coordinator) if index else MAIN
    bound = next(
        b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "start_cooking"
    )
    start = LocalThingsButton(coordinator, bound)
    other_href = f"/operational/state/vs/{1 - index}"
    other_before = dict(coordinator.resource(other_href) or {})
    _FakeConnection.log.clear()

    await _timer(coordinator, cavity).async_set_native_value(15)
    await start.async_press()
    await start.async_press()

    assert start.available
    assert _writes() == [
        (f"/devices/{index}", {"Device": {"Operation": {"operationTime": "00:15:00"}}})
    ]
    actual = coordinator.resource(cavity.to_actual(cook.OPERATION_HREF))
    assert actual[PREFIX + "operationTime"] == "00:15:00"
    assert actual[PREFIX + "remainingTime"] == "00:01:47"
    assert actual[PREFIX + "state"] == "Run"
    assert (coordinator.resource(other_href) or {}) == other_before


async def test_lower_start_ignores_other_cavity_held_settings(hass):
    coordinator = await _discovered(hass, _running_routes())
    await _timer(coordinator, MAIN).async_set_native_value(10)
    held = dict(coordinator._held_cooks[MAIN].values)
    assert held
    _FakeConnection.log.clear()

    await coordinator.async_start_cooking(_lower(coordinator))

    assert _writes() == []
    assert coordinator._held_cooks[MAIN].values == held


async def test_lower_start_does_not_use_main_running_state(hass):
    routes = _running_routes(0, True, "UpperConvectionBake")
    lower = {**LOWER, "Operation": {**LOWER["Operation"], "state": "Pause"}}
    routes.update(_routes(upper=routes["/devices/0"][1]["Device"], lower=lower))
    coordinator = await _discovered(hass, routes)
    _FakeConnection.log.clear()

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_start_cooking(_lower(coordinator))

    assert err.value.translation_key == "cook_start_not_idle"
    assert _writes() == []


@pytest.mark.parametrize("minutes", [0, 3])
async def test_refused_running_timer_edit_reports_error_without_replay(hass, minutes):
    routes = _running_routes()
    routes[("PUT", "/devices/1")] = (
        400,
        {"errorDescription": "the format of request json is wrong"},
    )
    coordinator = await _discovered(hass, routes)
    _FakeConnection.log.clear()

    with pytest.raises(HomeAssistantError) as err:
        await _timer(coordinator, _lower(coordinator)).async_set_native_value(minutes)

    assert err.value.translation_key == "command_refused"
    assert err.value.translation_placeholders == {
        "href": "/operational/state/vs/1",
        "error": "4.00 the format of request json is wrong",
    }
    assert len(_writes()) == 1
    assert not coordinator._held_cooks


@pytest.mark.parametrize("minutes", [0, 3])
async def test_running_timer_edit_requires_remote_control(hass, minutes):
    routes = _running_routes()
    routes["/devices/1/configuration"] = (
        200,
        {"Configuration": {"remoteControlEnabled": False}},
    )
    coordinator = await _discovered(hass, routes)
    _FakeConnection.log.clear()

    with pytest.raises(ServiceValidationError) as err:
        await _timer(coordinator, _lower(coordinator)).async_set_native_value(minutes)

    assert err.value.translation_key == "remote_control_disabled"
    assert not _writes()


@pytest.mark.parametrize("minutes", [0, 3])
async def test_idle_timer_edit_remains_pending(hass, minutes):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator)
    mode = next(b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "oven_mode")
    await coordinator.async_send_command(mode, "LowerBake")
    _FakeConnection.log.clear()

    await _timer(coordinator, cavity).async_set_native_value(minutes)

    assert coordinator._held_cooks[cavity].values[cook.PARAM_DURATION] == minutes * 60
    assert not _writes()


@pytest.mark.parametrize(
    "model",
    [
        None,
        123,
        "LCD_OV_WALL",
        "LCD_OV_WALL_16K_OTHER|1|2",
        "LCD_OV_WALL_20K|1|2",
        "TP2X_KS_WALLOVEN|1|2",
    ],
)
def test_other_models_keep_the_existing_timer_body(model):
    resources = {"/information/vs/0": {PREFIX + "modelNum": model}}

    assert oven._cook_time_write(3, {}, resources=resources) == (
        ["operational", "state", "vs", "0"],
        {PREFIX + "operationTime": "00:03:00", PREFIX + "remainingTime": "00:03:00"},
    )

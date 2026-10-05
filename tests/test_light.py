from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from homeassistant.components.light import ColorMode

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.light import LocalThingsLight
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import LightDesc


def _light(value, *, supports_brightness: bool):
    light = LocalThingsLight.__new__(LocalThingsLight)
    send_command = AsyncMock()
    light.coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(data={"lamp": value}, async_send_command=send_command),
    )
    light._state_key = "lamp"
    light._bound = cast(BoundEntity, object())
    light._supports_brightness = supports_brightness
    return light, send_command


def test_brightness_light_reports_off_low_high_and_unknown():
    light, _send_command = _light(0, supports_brightness=True)
    assert light.is_on is False
    assert light.brightness == 0

    light.coordinator.data["lamp"] = 128
    assert light.is_on is True
    assert light.brightness == 128

    light.coordinator.data["lamp"] = 255
    assert light.is_on is True
    assert light.brightness == 255

    light.coordinator.data["lamp"] = None
    assert light.is_on is None
    assert light.brightness is None


async def test_brightness_light_commands_brightness_and_off():
    light, send_command = _light(0, supports_brightness=True)

    await light.async_turn_on(brightness=128)
    send_command.assert_awaited_once_with(light._bound, 128)

    send_command.reset_mock()
    await light.async_turn_off()
    send_command.assert_awaited_once_with(light._bound, 0)


async def test_brightness_light_turn_on_defaults_to_high():
    light, send_command = _light(0, supports_brightness=True)
    await light.async_turn_on()
    send_command.assert_awaited_once_with(light._bound, 255)


async def test_onoff_light_has_no_brightness_and_sends_booleans():
    light, send_command = _light(False, supports_brightness=False)
    assert light.is_on is False
    assert light.brightness is None

    await light.async_turn_on(brightness=128)
    send_command.assert_awaited_once_with(light._bound, True)

    send_command.reset_mock()
    await light.async_turn_off()
    send_command.assert_awaited_once_with(light._bound, False)


def test_light_constructor_selects_the_matching_color_mode():
    coordinator = cast(LocalThingsCoordinator, object())
    for supports_brightness, color_mode in (
        (True, ColorMode.BRIGHTNESS),
        (False, ColorMode.ONOFF),
    ):
        bound = cast(
            BoundEntity,
            SimpleNamespace(desc=LightDesc(key="lamp", supports_brightness=supports_brightness)),
        )
        with patch.object(LocalThingsEntity, "__init__", return_value=None):
            light = LocalThingsLight(coordinator, bound)
        assert light.color_mode == color_mode
        assert light.supported_color_modes == {color_mode}

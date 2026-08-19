import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.light import LocalThingsLight
from custom_components.localthings.registry.discovery import BoundEntity


def _light(brightness):
    light = LocalThingsLight.__new__(LocalThingsLight)
    send_command = AsyncMock()
    light.coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(data={"lamp": brightness}, async_send_command=send_command),
    )
    light._state_key = "lamp"
    light._bound = cast(BoundEntity, object())
    return light, send_command


def test_light_reports_off_low_and_high():
    light, _send_command = _light(0)
    assert light.is_on is False
    assert light.brightness == 0

    light.coordinator.data["lamp"] = 128
    assert light.is_on is True
    assert light.brightness == 128

    light.coordinator.data["lamp"] = 255
    assert light.is_on is True
    assert light.brightness == 255


def test_light_reports_unknown_state():
    light, _send_command = _light(None)
    assert light.is_on is None
    assert light.brightness is None


def test_light_commands_brightness_and_off():
    light, send_command = _light(0)

    asyncio.run(light.async_turn_on(brightness=128))
    send_command.assert_awaited_once_with(light._bound, 128)

    send_command.reset_mock()
    asyncio.run(light.async_turn_off())
    send_command.assert_awaited_once_with(light._bound, 0)


def test_light_turn_on_defaults_to_high():
    light, send_command = _light(0)

    asyncio.run(light.async_turn_on())
    send_command.assert_awaited_once_with(light._bound, 255)

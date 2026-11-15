"""Remote keys: sent to /sec/tv/remotecontrol, never merged into state.

Measured on the UE50TU7172: the resource takes {keyvalue, keystatus:
"pressAndRelease"} (KEY_VOLUP raised the volume) but never answers a GET,
so nothing the remote sends may show up as if the TV had reported it.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.localthings.const import DOMAIN
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv


async def test_a_key_is_sent_and_not_cached_and_does_not_wait_for_a_poll(
    hass: HomeAssistant,
) -> None:
    session = await _setup_tv(hass, _entry(hass))
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    remote_id = hass.states.async_all("remote")[0].entity_id

    with patch.object(coordinator, "async_request_refresh") as refresh:
        await hass.services.async_call(
            "remote",
            "send_command",
            {"entity_id": remote_id, "command": "KEY_VOLUP"},
            blocking=True,
        )

    assert (
        ["sec", "tv", "remotecontrol"],
        {"x.com.samsung.tv.keyvalue": "KEY_VOLUP", "x.com.samsung.tv.keystatus": "pressAndRelease"},
    ) in session.writes
    assert coordinator.resource("/sec/tv/remotecontrol") == {}
    refresh.assert_not_called()


async def test_a_held_key_is_a_press_then_a_release(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))
    remote_id = hass.states.async_all("remote")[0].entity_id

    with patch("custom_components.localthings.remote.asyncio.sleep") as sleep:
        await hass.services.async_call(
            "remote",
            "send_command",
            {"entity_id": remote_id, "command": "KEY_FF", "hold_secs": 2},
            blocking=True,
        )

    keys = [
        body["x.com.samsung.tv.keystatus"]
        for path, body in session.writes
        if path == ["sec", "tv", "remotecontrol"]
    ]
    assert keys == ["press", "release"]
    sleep.assert_any_await(2.0)

"""A device's own SSDP announcements, as the event that it came on.

Samsung TVs and soundbars announce UPnP services (DIAL, a media renderer)
when they come on. Home Assistant's SSDP scanner reports an alive for a
device it did not already list: after a byebye, so on every power-on of a
TV that announced its standby on switching off. Without the `ssdp`
integration loaded this watches nothing, and polling alone finds the device.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback


async def async_watch_alive(
    hass: HomeAssistant, host: str, on_alive: Callable[[], None]
) -> Callable[[], None] | None:
    """Call `on_alive` for each SSDP alive from `host`; returns the remover."""
    if "ssdp" not in hass.config.components:
        return None
    from homeassistant.components import ssdp

    alive = ssdp.SsdpChange.ALIVE

    @callback
    def _seen(_info: Any, change: Any) -> None:
        if change is alive:
            on_alive()

    return await ssdp.async_register_callback(hass, _seen, {"_host": host})

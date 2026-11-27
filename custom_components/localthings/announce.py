"""A TV's own SSDP alive, as the event that it came on.

A TV announces its UPnP services on power-on, after the byebye it sent on
switching off (QE85QN95A). Without the `ssdp` integration this watches nothing.
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

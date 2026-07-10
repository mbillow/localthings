"""Diagnostics support for Local Things.

Downloadable from Settings > Devices & Services > this integration's
device > the menu > Download diagnostics. This is what the Repairs issue
(raised in coordinator.py when capability coverage is incomplete) points
users at: a redacted snapshot of the device's raw /device/0 state, plus
enough version/coverage metadata to reproduce and diagnose the gap.
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .registry.redact import redact_resources


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    integration = await async_get_integration(hass, DOMAIN)

    return {
        "device_type": coordinator.device_type_name or "unknown",
        "one_ui_version": coordinator.one_ui_version,
        "unbound_hrefs": sorted(coordinator._unbound_hrefs),
        "resources": redact_resources(coordinator.last_resources),
        "integration_version": integration.version,
        "smartthings_local_version": pkg_version("smartthings-local"),
        "observe_mode": coordinator.observe_mode,
        "observe_subscribed_hrefs": sorted(coordinator._observe.subscribed_hrefs),
        "observe_fallback_hrefs": sorted(coordinator._observe.fallback_hrefs),
        "observe_last_mode_change": coordinator._observe.last_mode_change_wall,
        "observe_href_freshness_s": {
            href: coordinator._cache.freshness_s(href)
            for href in coordinator._observe.subscribed_hrefs
        },
    }

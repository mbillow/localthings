"""Local Things — Samsung appliance local control integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_HOST, DOMAIN, PLATFORMS
from .coordinator import LocalThingsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = LocalThingsCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to device: {err}") from err
    # A refresh can return without the device having identified itself (the
    # session came up but /device/0 carried no serial yet). coordinator's
    # device_serial is still the host placeholder at that point, so setting up
    # platforms now bakes the host into every unique_id and registers a second,
    # IP-keyed device that outlives the mistake -- its connection_mode sensor
    # then collides with the real one forever. Retry instead; the placeholder
    # only exists to bridge the gap before the first successful poll.
    if not coordinator.discovered:
        raise ConfigEntryNotReady(
            f"Device at {entry.data[CONF_HOST]} has not reported its identity yet")
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: LocalThingsCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
    return unloaded

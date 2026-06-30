"""Button platform for Local Things."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ocf.registry.entities import ButtonDesc

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsButton(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, ButtonDesc)
    )


class LocalThingsButton(LocalThingsEntity, ButtonEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._payload = bound.desc.payload

    async def async_press(self) -> None:
        await self.coordinator.async_send_command(self._bound, self._payload)

"""Switch platform for Local Things."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ocf.registry.entities import SwitchDesc

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
        LocalThingsSwitch(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, SwitchDesc)
    )


class LocalThingsSwitch(LocalThingsEntity, SwitchEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: SwitchDesc = bound.desc
        self._attr_device_class = desc.device_class

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get(self._state_key)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, 'On')

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, 'Off')

"""Binary sensor platform for Local Things."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ocf.registry.entities import BinarySensorDesc

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsBinarySensor(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, BinarySensorDesc) and _is_included(b, coordinator)
    )


class LocalThingsBinarySensor(LocalThingsEntity, BinarySensorEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: BinarySensorDesc = bound.desc
        self._attr_device_class = desc.device_class

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get(self._state_key)

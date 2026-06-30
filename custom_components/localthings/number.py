"""Number platform for Local Things."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from samsung_appliance.registry.entities import NumberDesc

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
        LocalThingsNumber(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, NumberDesc)
    )


class LocalThingsNumber(LocalThingsEntity, NumberEntity):

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: NumberDesc = bound.desc
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class
        if desc.native_min is not None:
            self._attr_native_min_value = desc.native_min
        if desc.native_max is not None:
            self._attr_native_max_value = desc.native_max
        if desc.step is not None:
            self._attr_native_step = desc.step

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._state_key)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_command(self._bound, value)

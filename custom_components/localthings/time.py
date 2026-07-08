"""Time platform for Local Things."""
from __future__ import annotations

import datetime

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import TimeDesc

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
        LocalThingsTime(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, TimeDesc) and _is_included(b, coordinator)
    )


class LocalThingsTime(LocalThingsEntity, TimeEntity):

    @property
    def native_value(self) -> datetime.time | None:
        return (self.coordinator.data or {}).get(self._state_key)

    async def async_set_value(self, value: datetime.time) -> None:
        await self.coordinator.async_send_command(self._bound, value)

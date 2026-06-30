"""Select platform for Local Things."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ocf.registry.entities import SelectDesc

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _include(b) -> bool:
        if not isinstance(b.desc, SelectDesc):
            return False
        # Skip at registration time if exists_fn says the entity isn't present
        if b.desc.exists_fn is not None:
            rep = coordinator.last_resources.get(b.href) or {}
            if not b.desc.exists_fn(rep):
                return False
        return True

    async_add_entities(
        LocalThingsSelect(coordinator, b)
        for b in coordinator.bound
        if _include(b)
    )


class LocalThingsSelect(LocalThingsEntity, SelectEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: SelectDesc = bound.desc
        if not desc.options_field:
            self._attr_options = list(desc.options)

    @property
    def options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if desc.options_field:
            rep = self.coordinator.last_resources.get(self._bound.href) or {}
            return list(rep.get(desc.options_field) or [])
        return self._attr_options

    @property
    def current_option(self):
        return (self.coordinator.data or {}).get(self._state_key)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_send_command(self._bound, option)

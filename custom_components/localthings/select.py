"""Select platform for Local Things."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import SelectDesc

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
        LocalThingsSelect(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, SelectDesc) and _is_included(b, coordinator)
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
            raw = list(rep.get(desc.options_field) or [])
        else:
            raw = self._attr_options
        if desc.option_names:
            return [desc.option_names.get(o, o) for o in raw]
        return raw

    @property
    def current_option(self):
        desc: SelectDesc = self._bound.desc
        raw = (self.coordinator.data or {}).get(self._state_key)
        if desc.option_names:
            return desc.option_names.get(raw, raw)
        return raw

    async def async_select_option(self, option: str) -> None:
        desc: SelectDesc = self._bound.desc
        if desc.option_names:
            reverse = {v: k for k, v in desc.option_names.items()}
            option = reverse.get(option, option)
        await self.coordinator.async_send_command(self._bound, option)

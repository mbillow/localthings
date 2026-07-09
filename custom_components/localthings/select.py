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


def _normalize(value):
    """HA option/state values must be lowercase to serve as translation keys.

    Samsung's raw enum values are upper snake case (e.g.
    CV_TTYPE_RF9000A_FREEZE); the device still expects that exact casing
    back on write, so callers must map the normalized value back via
    _raw_options() before sending a command.
    """
    return value.lower() if isinstance(value, str) else value


class LocalThingsSelect(LocalThingsEntity, SelectEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: SelectDesc = bound.desc
        if not desc.options_field:
            self._attr_options = [_normalize(o) for o in desc.options]

    def _raw_options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if desc.options_field:
            rep = self.coordinator.last_resources.get(self._bound.href) or {}
            return list(rep.get(desc.options_field) or [])
        return list(desc.options)

    @property
    def options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if desc.options_field:
            return [_normalize(o) for o in self._raw_options()]
        return self._attr_options

    @property
    def current_option(self):
        raw = (self.coordinator.data or {}).get(self._state_key)
        return _normalize(raw)

    async def async_select_option(self, option: str) -> None:
        raw = next(
            (o for o in self._raw_options() if _normalize(o) == option), option
        )
        await self.coordinator.async_send_command(self._bound, raw)

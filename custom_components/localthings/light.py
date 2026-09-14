"""Light platform for Local Things."""

from __future__ import annotations

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.entities import LightDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsLight(coordinator, bound)
        for bound in coordinator.bound
        if isinstance(bound.desc, LightDesc) and _is_included(bound, coordinator)
    )


class LocalThingsLight(LocalThingsEntity, LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        brightness = self.brightness
        return brightness > 0 if brightness is not None else None

    @property
    def brightness(self) -> int | None:
        value = (self.coordinator.data or {}).get(self._state_key)
        return int(value) if value is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        await self.coordinator.async_send_command(self._bound, brightness)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, 0)

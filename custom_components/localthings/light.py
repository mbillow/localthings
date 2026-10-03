"""Light platform for LocalThings."""

from __future__ import annotations

from typing import cast

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
    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc = cast(LightDesc, bound.desc)
        self._supports_brightness = desc.supports_brightness
        color_mode = ColorMode.BRIGHTNESS if self._supports_brightness else ColorMode.ONOFF
        self._attr_color_mode = color_mode
        self._attr_supported_color_modes = {color_mode}

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get(self._state_key)
        return bool(value) if value is not None else None

    @property
    def brightness(self) -> int | None:
        if not self._supports_brightness:
            return None
        value = (self.coordinator.data or {}).get(self._state_key)
        return int(value) if value is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        payload = kwargs.get(ATTR_BRIGHTNESS, 255) if self._supports_brightness else True
        await self.coordinator.async_send_command(self._bound, payload)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(
            self._bound, 0 if self._supports_brightness else False
        )

"""Button platform for Local Things."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities import cook
from .registry.entities import ButtonDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsButton(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, ButtonDesc) and _is_included(b, coordinator)
    )


class LocalThingsButton(LocalThingsEntity, ButtonEntity):
    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._payload = bound.desc.payload
        self._payload_fn = bound.desc.payload_fn

    async def async_press(self) -> None:
        payload = self._payload_fn(dt_util.now()) if self._payload_fn else self._payload
        try:
            await self.coordinator.async_send_command(self._bound, payload)
        except ServiceValidationError as err:
            # Running timer edits already write through (#662); a subsequent
            # Start press should leave that cook alone.
            if (
                self._bound.desc.cook_param == cook.PARAM_START
                and err.translation_key == "cook_start_not_idle"
                and (self._resources.get(cook.OPERATION_HREF) or {}).get("x.com.samsung.da.state")
                == "Run"
            ):
                return
            raise

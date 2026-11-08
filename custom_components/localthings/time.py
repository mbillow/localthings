"""Time platform for Local Things."""

from __future__ import annotations

import datetime
from typing import cast

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, async_add_bound_entities
from .registry.entities import TimeDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_bound_entities(
        entry,
        coordinator,
        async_add_entities,
        (b for b in coordinator.bound if isinstance(b.desc, TimeDesc)),
        lambda b: LocalThingsTime(coordinator, b),
    )


class LocalThingsTime(LocalThingsEntity, TimeEntity):
    @property
    def native_value(self) -> datetime.time | None:
        value = (self.coordinator.data or {}).get(self._state_key)
        # A descriptor projecting a device-reported *duration* onto the clock
        # has no timezone to do it in, so it hands over a UTC instant and the
        # conversion lands here (operational.delay_finish_at). Every other
        # time entity reads a wall-clock field straight off the device.
        if isinstance(value, datetime.datetime):
            return dt_util.as_local(value).time()
        return value

    async def async_set_value(self, value: datetime.time) -> None:
        desc = cast(TimeDesc, self._bound.desc)
        payload = desc.payload_fn(value, dt_util.now()) if desc.payload_fn else value
        await self.coordinator.async_send_command(self._bound, payload)

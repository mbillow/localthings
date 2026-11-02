"""Update platform for Local Things (soundbar).

One read-only firmware entity; no install request is known to work. Only
'noupdate' (updateProgress -1) has been seen on /sec/networkaudio/swUpdate,
so reading updateProgress 0..100 as an update underway is a guess. The TV
reports no pending update, so it gets no update entity.
"""

from __future__ import annotations

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.entities import UpdateDesc

_FIELD = "x.com.samsung.networkaudio."


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsUpdate(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, UpdateDesc) and _is_included(b, coordinator)
    )


class LocalThingsUpdate(LocalThingsEntity, UpdateEntity):
    """Firmware update entity for an AV board."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.PROGRESS

    def _status(self) -> dict:
        return self.coordinator.resource(self._bound.href)

    @property
    def installed_version(self) -> str | None:
        # The device card's firmware; swUpdate doesn't report it.
        return self.coordinator.device_info.get("sw_version")

    @property
    def latest_version(self) -> str | None:
        installed = self.installed_version
        rep = self._status()
        if not rep or rep.get(f"{_FIELD}updateStatus") == "noupdate":
            return installed
        candidate = rep.get(f"{_FIELD}updateVersion")
        if not candidate or candidate == "0":
            return installed
        return str(candidate)

    @property
    def in_progress(self) -> bool:
        return self.update_percentage is not None

    @property
    def update_percentage(self) -> int | None:
        rep = self._status()
        if rep.get(f"{_FIELD}updateStatus") == "noupdate":
            return None
        progress = rep.get(f"{_FIELD}updateProgress")
        if isinstance(progress, int) and 0 <= progress <= 100:
            return progress
        return None

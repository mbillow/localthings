"""Base entity for Local Things."""
from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from samsung_appliance.registry.adapter import _key
from samsung_appliance.registry.discovery import BoundEntity

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator


class LocalThingsEntity(CoordinatorEntity[LocalThingsCoordinator]):
    """Base class for all Local Things entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LocalThingsCoordinator, bound: BoundEntity) -> None:
        super().__init__(coordinator)
        self._bound = bound
        self._state_key = _key(bound)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_serial}_{self._state_key}"
        self._attr_name = bound.desc.name
        self._attr_icon = bound.desc.icon
        self._attr_entity_category = bound.desc.entity_category
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

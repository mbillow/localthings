"""Base entity for Local Things."""
from __future__ import annotations

import re

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import EntityCategory

from .ocf.registry.adapter import _key
from .ocf.registry.discovery import BoundEntity

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator


def _derive_name(state_key: str) -> str:
    """Turn a snake_case state key into a title-cased display name.

    Strips a trailing instance number of 0 (singleton), promotes any other
    instance number with a space: "door_cooler_open1" → "Door Cooler Open 1".
    """
    name = re.sub(r'(\d+)$', lambda m: f' {m.group()}' if int(m.group()) > 0 else '', state_key)
    return name.replace('_', ' ').title().strip()


class LocalThingsEntity(CoordinatorEntity[LocalThingsCoordinator]):
    """Base class for all Local Things entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LocalThingsCoordinator, bound: BoundEntity) -> None:
        super().__init__(coordinator)
        self._bound = bound
        self._state_key = _key(bound)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_serial}_{self._state_key}"
        self._attr_name = bound.desc.name if bound.desc.name is not None else _derive_name(self._state_key)
        self._attr_icon = bound.desc.icon
        raw_cat = bound.desc.entity_category
        self._attr_entity_category = EntityCategory(raw_cat) if raw_cat else None
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

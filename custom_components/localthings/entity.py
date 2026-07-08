"""Base entity for Local Things."""
from __future__ import annotations

import re

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import EntityCategory

from .registry.adapter import _key
from .registry.discovery import BoundEntity

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator


def _is_included(bound: BoundEntity, coordinator: 'LocalThingsCoordinator') -> bool:
    """Return False if the entity should not be registered for this device.

    Explicit exists_fn takes priority. Otherwise, if the entity has a field,
    require that field to be present in the resource rep so that optional
    fields on shared resources don't create phantom entities.

    An empty rep ({}) means /device/0 returned a stub for this resource —
    the resource exists on the device but data hasn't been fetched yet.
    In that case we include the entity so it can be populated by sub-polls.
    """
    rep = coordinator.last_resources.get(bound.href)
    if rep is None:
        return False
    if bound.desc.exists_fn is not None:
        return bound.desc.exists_fn(rep)
    if bound.desc.field:
        if not rep:  # stub — resource known to exist, data not yet fetched
            return True
        return bound.desc.field in rep
    return True  # rep_fn or no-field entities (ButtonDesc) are always included


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
        self._attr_translation_key = bound.desc.translation_key
        self._attr_icon = bound.desc.icon
        raw_cat = bound.desc.entity_category
        self._attr_entity_category = EntityCategory(raw_cat) if raw_cat else None
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

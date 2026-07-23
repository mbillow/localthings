"""Number platform for Local Things."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import NumberDesc

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
        LocalThingsNumber(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, NumberDesc) and _is_included(b, coordinator)
    )


class LocalThingsNumber(LocalThingsEntity, NumberEntity):

    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: NumberDesc = bound.desc
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class
        if desc.native_min is not None:
            self._attr_native_min_value = desc.native_min
        if desc.native_max is not None:
            self._attr_native_max_value = desc.native_max
        if desc.step is not None:
            self._attr_native_step = desc.step

    @property
    def native_unit_of_measurement(self):
        desc: NumberDesc = self._bound.desc
        if desc.unit_fn is not None:
            return desc.unit_fn(self.coordinator.resource(self._bound.href))
        return self._attr_native_unit_of_measurement

    def _range_from_resource(self) -> list | None:
        desc: NumberDesc = self._bound.desc
        if not desc.range_field:
            return None
        r = self.coordinator.resource(self._bound.href).get(desc.range_field)
        return r if (isinstance(r, (list, tuple)) and len(r) == 2) else None

    @property
    def native_min_value(self) -> float:
        desc: NumberDesc = self._bound.desc
        if desc.native_min_fn is not None:
            return desc.native_min_fn(self.coordinator.resource(self._bound.href))
        r = self._range_from_resource()
        if r is not None:
            return float(r[0])
        if hasattr(self, '_attr_native_min_value'):
            return self._attr_native_min_value
        return super().native_min_value

    @property
    def native_max_value(self) -> float:
        desc: NumberDesc = self._bound.desc
        if desc.native_max_fn is not None:
            return desc.native_max_fn(self.coordinator.resource(self._bound.href))
        r = self._range_from_resource()
        if r is not None:
            return float(r[1])
        if hasattr(self, '_attr_native_max_value'):
            return self._attr_native_max_value
        return super().native_max_value

    @property
    def native_step(self) -> float:
        desc: NumberDesc = self._bound.desc
        if desc.step_fn is not None:
            return desc.step_fn(self.coordinator.resource(self._bound.href))
        if hasattr(self, '_attr_native_step'):
            return self._attr_native_step
        return super().native_step

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._state_key)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_command(self._bound, value)

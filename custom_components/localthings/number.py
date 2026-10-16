"""Number platform for Local Things."""

from __future__ import annotations

from typing import cast

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import DurationConverter

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities import cook
from .registry.entities import NumberDesc


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
    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc = cast(NumberDesc, bound.desc)
        self._attr_mode = NumberMode(desc.mode)
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = (
            NumberDeviceClass(desc.device_class) if desc.device_class else None
        )
        if desc.native_min is not None:
            self._attr_native_min_value = desc.native_min
        if desc.native_max is not None:
            self._attr_native_max_value = desc.native_max
        if desc.step is not None:
            self._attr_native_step = desc.step

    @property
    def native_unit_of_measurement(self):
        desc = cast(NumberDesc, self._bound.desc)
        if self._is_cook_duration() and self._number_option_unit_of_measurement:
            return self._number_option_unit_of_measurement
        if desc.unit_fn is not None:
            return desc.unit_fn(self.coordinator.resource(self._bound.href))
        return self._attr_native_unit_of_measurement

    def _is_cook_duration(self) -> bool:
        return (
            self._bound.desc.cook_param == cook.PARAM_DURATION
            and self.device_class == NumberDeviceClass.DURATION
        )

    def _from_minutes(self, value: float) -> float:
        # HA 2026.9 rounds converted integer minutes to whole hours and leaves
        # the step unchanged. Convert the entire number together instead.
        if self._is_cook_duration():
            return DurationConverter.convert(
                value, UnitOfTime.MINUTES, self.native_unit_of_measurement or UnitOfTime.MINUTES
            )
        return value

    def _live_bounds(self) -> tuple[float, float, float] | None:
        desc = cast(NumberDesc, self._bound.desc)
        if desc.bounds_fn is None:
            return None
        return desc.bounds_fn(self.coordinator.resource(self._bound.href), self._resources)

    def _range_from_resource(self) -> list | None:
        desc = cast(NumberDesc, self._bound.desc)
        if not desc.range_field:
            return None
        r = self.coordinator.resource(self._bound.href).get(desc.range_field)
        return r if (isinstance(r, (list, tuple)) and len(r) == 2) else None

    @property
    def native_min_value(self) -> float:
        bounds = self._live_bounds()
        if bounds is not None:
            return self._from_minutes(bounds[0])
        desc = cast(NumberDesc, self._bound.desc)
        if desc.native_min_fn is not None:
            return self._from_minutes(
                desc.native_min_fn(self.coordinator.resource(self._bound.href))
            )
        r = self._range_from_resource()
        if r is not None:
            return self._from_minutes(float(r[0]))
        if hasattr(self, "_attr_native_min_value"):
            return self._from_minutes(self._attr_native_min_value)
        return self._from_minutes(super().native_min_value)

    @property
    def native_max_value(self) -> float:
        bounds = self._live_bounds()
        if bounds is not None:
            return self._from_minutes(bounds[1])
        desc = cast(NumberDesc, self._bound.desc)
        if desc.native_max_fn is not None:
            return self._from_minutes(
                desc.native_max_fn(self.coordinator.resource(self._bound.href))
            )
        r = self._range_from_resource()
        if r is not None:
            return self._from_minutes(float(r[1]))
        if hasattr(self, "_attr_native_max_value"):
            return self._from_minutes(self._attr_native_max_value)
        return self._from_minutes(super().native_max_value)

    @property
    def native_step(self) -> float | None:
        bounds = self._live_bounds()
        if bounds is not None:
            return self._from_minutes(bounds[2])
        desc = cast(NumberDesc, self._bound.desc)
        if desc.step_fn is not None:
            return self._from_minutes(desc.step_fn(self.coordinator.resource(self._bound.href)))
        if hasattr(self, "_attr_native_step"):
            return self._from_minutes(self._attr_native_step)
        step = super().native_step
        return self._from_minutes(step) if step is not None else None

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get(self._state_key)
        if (
            value == 0
            and (self._is_cook_duration() or self._bound.desc.cook_param in cook.DURATION_PARTS)
            and self.coordinator.cook_time_is_unset(self._bound.subdevice)
        ):
            return None
        return self._from_minutes(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        if self._is_cook_duration():
            value = round(
                DurationConverter.convert(
                    value, self.native_unit_of_measurement or UnitOfTime.MINUTES, UnitOfTime.MINUTES
                )
            )
        await self.coordinator.async_send_command(self._bound, value)

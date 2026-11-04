"""Vacuum platform for Samsung robot vacuums (issue #659).

State and commands are `<Group>_<Value>` tokens on `/mode/vs/0`; see
registry/capabilities/robot_vacuum.py for the vocabulary. Each feature is
offered only when the robot lists its token in supportedModes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities import robot_vacuum as rv
from .registry.entities import VacuumDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsVacuum(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, VacuumDesc) and _is_included(b, coordinator)
    )


class LocalThingsVacuum(LocalThingsEntity, StateVacuumEntity):
    """A robot vacuum driven through its /mode/vs/0 tokens."""

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None

    @property
    def _rep(self) -> dict:
        return self._resources.get(self._bound.href) or {}

    def _supports(self, group: str, value: str) -> bool:
        return rv.supports(self._rep, group, value)

    @property
    def supported_features(self) -> VacuumEntityFeature:
        features = VacuumEntityFeature.STATE | VacuumEntityFeature.SEND_COMMAND
        for feature, group, value in (
            (VacuumEntityFeature.START, rv.CLEANING, rv.START_PROGRAM),
            (VacuumEntityFeature.STOP, rv.CLEANING, rv.STOP_PROGRAM),
            (VacuumEntityFeature.CLEAN_SPOT, rv.CLEANING, rv.SPOT_PROGRAM),
            (VacuumEntityFeature.PAUSE, rv.CONTROL, "Pause"),
            (VacuumEntityFeature.RETURN_HOME, rv.CONTROL, "Homing"),
        ):
            if self._supports(group, value):
                features |= feature
        if rv.fan_speed_list(self._rep):
            features |= VacuumEntityFeature.FAN_SPEED
        return features

    @property
    def activity(self) -> VacuumActivity | None:
        value = rv.activity(self._rep)
        return VacuumActivity(value) if value is not None else None

    @property
    def fan_speed(self) -> str | None:
        return rv.fan_speed(rv.group_value(self._rep, rv.TURBO))

    @property
    def fan_speed_list(self) -> list[str]:
        return rv.fan_speed_list(self._rep)

    async def _send(self, group: str, value: str) -> None:
        if not self._supports(group, value):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="vacuum_unsupported_command",
                translation_placeholders={"command": f"{group}_{value}"},
            )
        await self.coordinator.async_send_command(self._bound, (group, value))

    async def async_start(self) -> None:
        # From a pause, resume the run. A robot that can't resume restarts it.
        if self.activity == VacuumActivity.PAUSED and self._supports(rv.CONTROL, "Cleaning"):
            await self._send(rv.CONTROL, "Cleaning")
            return
        await self._send(rv.CLEANING, rv.START_PROGRAM)

    async def async_stop(self, **kwargs: Any) -> None:
        await self._send(rv.CLEANING, rv.STOP_PROGRAM)

    async def async_pause(self) -> None:
        await self._send(rv.CONTROL, "Pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self._send(rv.CONTROL, "Homing")

    async def async_clean_spot(self, **kwargs: Any) -> None:
        await self._send(rv.CLEANING, rv.SPOT_PROGRAM)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        value = rv.turbo_value(fan_speed, self._rep)
        if value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="vacuum_unsupported_fan_speed",
                translation_placeholders={"fan_speed": fan_speed},
            )
        await self._send(rv.TURBO, value)

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send any token the robot lists in supportedModes, e.g.
        `Cleaning_Repeat` or `Control_Point`. Tokens take no parameters."""
        if params:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="vacuum_command_params",
            )
        group, _, value = command.rpartition("_")
        await self._send(group, value)

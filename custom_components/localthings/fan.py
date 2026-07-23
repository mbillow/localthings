"""Fan platform for Samsung range hoods."""

from __future__ import annotations

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.entities import FanDesc


POWER_HREF = '/power/0'
POWER_VS_HREF = '/power/vs/0'
_FAN_SPEED_FIELD = 'x.com.samsung.da.hood.fanSpeed'
_SUPPORTED_FAN_SPEED_FIELD = 'x.com.samsung.da.hood.supportedFanSpeed'


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsRangeHoodFan(coordinator, bound)
        for bound in coordinator.bound
        if isinstance(bound.desc, FanDesc) and _is_included(bound, coordinator)
    )


class LocalThingsRangeHoodFan(LocalThingsEntity, FanEntity):
    """A hood fan combining sibling power and fan-speed resources."""

    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _all_speed_codes(self) -> list[str]:
        rep = self._rep(self._bound.href)
        return [str(value) for value in rep.get(_SUPPORTED_FAN_SPEED_FIELD, ())]

    def _active_speed_codes(self) -> list[str]:
        # Power is carried by the separate /power resource.  fanSpeed retains
        # the selected setting while power is off (as the lamp's `current`
        # field does), so every advertised code is an active ordered speed.
        return self._all_speed_codes()

    def _power_payload(self, enabled: bool) -> tuple[str, bool, str]:
        """Target whichever power resource this hood actually exposes."""
        resources = self.coordinator.last_resources
        target = POWER_HREF if POWER_HREF in resources else POWER_VS_HREF
        return 'power', enabled, target

    @property
    def is_on(self) -> bool:
        rep = self._rep(POWER_HREF)
        if 'value' in rep:
            return bool(rep.get('value'))
        return str(
            self._rep(POWER_VS_HREF).get('x.com.samsung.da.power', '')
        ).lower() == 'on'

    @property
    def speed_count(self) -> int:
        return len(self._active_speed_codes())

    @property
    def percentage(self) -> int | None:
        if not self.is_on:
            return 0
        codes = self._active_speed_codes()
        current = str(self._rep(self._bound.href).get(_FAN_SPEED_FIELD, ''))
        if not codes or current not in codes:
            return None
        return ordered_list_item_to_percentage(codes, current)

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        await self.coordinator.async_send_command(
            self._bound, self._power_payload(True),
        )
        if percentage is not None:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(
            self._bound, self._power_payload(False),
        )

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        codes = self._active_speed_codes()
        if not codes:
            return
        if not self.is_on:
            await self.coordinator.async_send_command(
                self._bound, self._power_payload(True),
            )
        code = percentage_to_ordered_list_item(codes, percentage)
        await self.coordinator.async_send_command(self._bound, ('speed', code))

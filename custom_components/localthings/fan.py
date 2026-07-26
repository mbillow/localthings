"""Fan platform for Samsung range hoods and air purifiers.

Two shapes share this platform, dispatched on the bound href in
async_setup_entry: range hoods combine sibling power + fan-speed resources
(/hood/*), while AVT-WW-TP1 air purifiers drive /wind/strength/vs/0 as a
preset-only fan that also owns the appliance's on/off.
"""

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

# Air-purifier wind-strength fan (matches air_purifier.WIND_STRENGTH.href).
_WIND_STRENGTH_HREF = '/wind/strength/vs/0'
_WIND_MODES_FIELD = 'x.com.samsung.da.modes'
_WIND_SUPPORTED_FIELD = 'x.com.samsung.da.supportedModes'
_WIND_NAMES_FIELD = 'x.com.samsung.da.modesName'
# The wind-strength codes are one flat set of user-selectable fan modes -- Auto
# sits alongside the manual speeds, not as a separate preset -- so all of them
# surface as fan preset_modes. Values are lowercase HA-style (auto/low/medium/
# high/sleep); the catalog (entity.fan.fan.state_attributes.preset_mode.state)
# title-cases them for display. low/medium/high replace the device's raw
# Mid/High/Turbo for the manual codes (2/3/4) -- HA's own vocabulary for a
# three-step fan, so the entity reads the same as every other fan in HA and
# matches what voice assistants and fan blueprints expect. It does mean the
# labels no longer match the SmartThings app word-for-word (the device's "High"
# is HA's "Medium"); the ordering is identical, and _labels() falls back to the
# device's own modesName for any code not in this map, so a board reporting an
# extra speed still gets a sensible label instead of a bare code.
_WIND_MODE_LABELS = {'0': 'auto', '2': 'low', '3': 'medium', '4': 'high', '91': 'sleep'}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[FanEntity] = []
    for bound in coordinator.bound:
        if not isinstance(bound.desc, FanDesc) or not _is_included(bound, coordinator):
            continue
        if bound.href == _WIND_STRENGTH_HREF:
            entities.append(LocalThingsAirPurifierFan(coordinator, bound))
        else:
            entities.append(LocalThingsRangeHoodFan(coordinator, bound))
    async_add_entities(entities)


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


class LocalThingsAirPurifierFan(LocalThingsEntity, FanEntity):
    """A Samsung air-purifier fan over /wind/strength/vs/0.

    The board's wind-strength codes are one flat set of user-selectable modes
    (Auto/Low/Medium/High/Sleep) -- Auto is a fan setting alongside the manual
    speeds, not a separate preset -- so all of them surface as fan preset_modes.
    Power is carried by the separate /power resource (the wind-strength code is
    retained while off), so is_on comes from there, not from the code.

    PRESET_MODE only, deliberately -- unlike LocalThingsRangeHoodFan above,
    which exposes SET_SPEED. The usual HA split (percentage for the ordered
    speeds, presets for named modes) would mean splitting supportedModes into
    two halves and presenting the manual codes as 33/66/100%. That reads as a
    continuous range the device doesn't have, and it hides Auto and Sleep behind
    a second control even though the device treats all five as one mutually
    exclusive selection on a single field -- picking Auto is the same kind of
    act as picking Turbo, and only one can be active. The hood differs because
    its fanSpeed genuinely is an ordered-only ladder with no named modes mixed
    in. Revisit if a board turns up that reports a real percentage range.
    """

    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _labels(self) -> dict[str, str]:
        """Advertised code -> display label, in the device's own order. Uses the
        friendly Auto/Low/Medium/High/Sleep names, falling back to the device's
        own modesName (then the raw code) for any code not in the fixed map."""
        rep = self._rep(self._bound.href)
        codes = [str(code) for code in rep.get(_WIND_SUPPORTED_FIELD, ())]
        names = [str(name) for name in rep.get(_WIND_NAMES_FIELD, ())]
        device = dict(zip(codes, names))
        return {c: _WIND_MODE_LABELS.get(c) or str(device.get(c) or c).lower()
                for c in codes}

    def _current_code(self) -> str | None:
        value = self._rep(self._bound.href).get(_WIND_MODES_FIELD)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        return None if value is None else str(value)

    def _power_payload(self, enabled: bool) -> tuple[str, bool, str]:
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
    def preset_modes(self) -> list[str]:
        return list(self._labels().values())

    @property
    def preset_mode(self) -> str | None:
        if not self.is_on:
            return None
        return self._labels().get(self._current_code())

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        code = next(
            (c for c, label in self._labels().items() if label == preset_mode),
            None,
        )
        if code is None:
            return
        if not self.is_on:
            await self.coordinator.async_send_command(
                self._bound, self._power_payload(True),
            )
        await self.coordinator.async_send_command(self._bound, ('mode', code))

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        await self.coordinator.async_send_command(
            self._bound, self._power_payload(True),
        )
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(
            self._bound, self._power_payload(False),
        )

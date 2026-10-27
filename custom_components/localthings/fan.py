"""Fan platform for Samsung range hoods and air purifiers.

Four FanDesc-bound hrefs exist, dispatched by href in async_setup_entry
below since each needs different HA fan semantics: the range hood's fan
speed is an ordered set of numeric levels (SET_SPEED). The older
ARTIK051_TVTL air-purifier family (issue #56) reports Auto/Sleep/Low/
Medium/High as one numeric code, split into Low/Medium/High speeds and
Auto/Sleep presets (#658). The TP1X air-purifier family's modes (Smart/Max/Mid/WindFree/Sleep, issue
#130) and the A-VTWW-TP2-21 family's /wind/strength/vs/0 modes (issue #151)
are both named behaviors with no linear order (PRESET_MODE) --
LocalThingsAirPurifierFan handles both hrefs, the only difference being
whether the label comes from supportedModes or a parallel modesName array
(see _label_for_code)."""

from __future__ import annotations

import logging

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
from .registry.capabilities.air_purifier import HREF_AIRFLOW
from .registry.capabilities.air_purifier import HREF_MODE as AIR_PURIFIER_FAN_HREF
from .registry.capabilities.air_purifier import (
    HREF_WIND_STRENGTH as AIR_PURIFIER_WIND_STRENGTH_HREF,
)
from .registry.entities import FanDesc

_LOGGER = logging.getLogger(__name__)

POWER_HREF = "/power/0"
POWER_VS_HREF = "/power/vs/0"
_FAN_SPEED_FIELD = "x.com.samsung.da.hood.fanSpeed"
_SUPPORTED_FAN_SPEED_FIELD = "x.com.samsung.da.hood.supportedFanSpeed"
_MIN_FAN_SPEED_FIELD = "x.com.samsung.da.hood.settableMinFanSpeed"
_MAX_FAN_SPEED_FIELD = "x.com.samsung.da.hood.settableMaxFanSpeed"
_OFF_SPEED_CODE = "0"

_MODES_FIELD = "x.com.samsung.da.modes"
_SUPPORTED_MODES_FIELD = "x.com.samsung.da.supportedModes"
_MODES_NAME_FIELD = "x.com.samsung.da.modesName"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for bound in coordinator.bound:
        if not (isinstance(bound.desc, FanDesc) and _is_included(bound, coordinator)):
            continue
        if bound.href in (AIR_PURIFIER_FAN_HREF, AIR_PURIFIER_WIND_STRENGTH_HREF):
            entities.append(LocalThingsAirPurifierFan(coordinator, bound))
        elif bound.href == HREF_AIRFLOW:
            entities.append(LocalThingsAirflowFan(coordinator, bound))
        else:
            entities.append(LocalThingsRangeHoodFan(coordinator, bound))
    async_add_entities(entities)


class LocalThingsRangeHoodFan(LocalThingsEntity, FanEntity):
    """A hood fan combining sibling power and fan-speed resources.

    Some boards that reuse this capability (built-in microwave vent fans,
    issues #137/#142) report no sibling `/power/0` or `/power/vs/0`
    resource at all -- fan speed 0 is itself the off state there.
    `_speed_zero_is_off` detects that shape and switches every method
    below to drive off/on purely through the fanSpeed field.

    Deliberately not the same question as `_has_separate_power`, which
    only proves some power resource exists on the device -- on a combi
    appliance that resource can belong to the cavity, not the vent fan,
    and toggling it from here would turn off the whole appliance.
    """

    _enable_turn_on_off_backwards_compatibility = False

    @property
    def supported_features(self) -> FanEntityFeature:
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self.speed_count > 0:
            features |= FanEntityFeature.SET_SPEED
        return features

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _has_separate_power(self) -> bool:
        return bool(self._rep(POWER_HREF)) or bool(self._rep(POWER_VS_HREF))

    def _speed_zero_is_off(self) -> bool:
        """Whether fan speed '0' is itself this hood's off step, with no
        separate power resource to toggle -- settableMinFanSpeed '0', or
        '0' inside supportedFanSpeed. False for the standalone hood, whose
        codes start at 14 and which carries a real /power resource."""
        rep = self._rep(self._bound.href)
        return (
            str(rep.get(_MIN_FAN_SPEED_FIELD, "")) == _OFF_SPEED_CODE
            or _OFF_SPEED_CODE in self._all_speed_codes()
        )

    def _all_speed_codes(self) -> list[str]:
        rep = self._rep(self._bound.href)
        supported = rep.get(_SUPPORTED_FAN_SPEED_FIELD)
        if supported:
            return [str(value) for value in supported]
        min_s = rep.get(_MIN_FAN_SPEED_FIELD)
        max_s = rep.get(_MAX_FAN_SPEED_FIELD)
        if min_s is not None and max_s is not None:
            try:
                mn, mx = int(min_s), int(max_s)
                return [str(i) for i in range(mn, mx + 1)]
            except (ValueError, TypeError):
                pass
        return []

    def _active_speed_codes(self) -> list[str]:
        codes = self._all_speed_codes()
        if self._speed_zero_is_off():
            return [code for code in codes if code != _OFF_SPEED_CODE]
        # Power is carried by the separate /power resource; fanSpeed
        # retains the selected setting while power is off, so every
        # advertised code is an active ordered speed.
        return codes

    def _power_payload(self, enabled: bool) -> tuple[str, bool, str]:
        """Target whichever power resource this hood actually exposes."""
        resources = self._resources
        target = POWER_HREF if POWER_HREF in resources else POWER_VS_HREF
        return "power", enabled, target

    @property
    def is_on(self) -> bool:
        if self._speed_zero_is_off():
            current = str(self._rep(self._bound.href).get(_FAN_SPEED_FIELD, "0"))
            return current not in ("", _OFF_SPEED_CODE)
        rep = self._rep(POWER_HREF)
        if "value" in rep:
            return bool(rep.get("value"))
        return str(self._rep(POWER_VS_HREF).get("x.com.samsung.da.power", "")).lower() == "on"

    @property
    def speed_count(self) -> int:
        return len(self._active_speed_codes())

    @property
    def percentage(self) -> int | None:
        if not self.is_on:
            return 0
        codes = self._active_speed_codes()
        current = str(self._rep(self._bound.href).get(_FAN_SPEED_FIELD, ""))
        if not codes or current not in codes:
            return None
        return ordered_list_item_to_percentage(codes, current)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        if self._speed_zero_is_off():
            if percentage is not None:
                await self.async_set_percentage(percentage)
                return
            if self.is_on:
                # Already running: no percentage given means "just turn on",
                # not "reset to the lowest speed".
                return
            codes = self._active_speed_codes()
            if codes:
                await self.coordinator.async_send_command(self._bound, ("speed", codes[0]))
            return
        await self.coordinator.async_send_command(
            self._bound,
            self._power_payload(True),
        )
        if percentage is not None:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs) -> None:
        if self._speed_zero_is_off():
            await self.coordinator.async_send_command(self._bound, ("speed", _OFF_SPEED_CODE))
            return
        await self.coordinator.async_send_command(
            self._bound,
            self._power_payload(False),
        )

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        codes = self._active_speed_codes()
        if not codes:
            return
        if not self._speed_zero_is_off() and not self.is_on:
            await self.coordinator.async_send_command(
                self._bound,
                self._power_payload(True),
            )
        code = percentage_to_ordered_list_item(codes, percentage)
        await self.coordinator.async_send_command(self._bound, ("speed", code))


class LocalThingsAirPurifierFan(LocalThingsEntity, FanEntity):
    """Air-purifier fan: named preset modes, not an ordered percentage --
    see air_purifier.FAN's comment for why (Smart/WindFree/Sleep aren't
    "faster/slower" than Max/Mid)."""

    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _mode_rep(self) -> dict:
        return self._rep(self._bound.href)

    def _power_payload(self, enabled: bool) -> tuple[str, bool, str]:
        """Target whichever power resource this unit actually exposes --
        same pattern as LocalThingsRangeHoodFan._power_payload above.
        Writing a hardcoded href here would silently no-op on a board that
        only reports the other one, even though is_on already falls back
        correctly."""
        resources = self._resources
        target = POWER_VS_HREF if POWER_VS_HREF in resources else POWER_HREF
        return "power", enabled, target

    @property
    def is_on(self) -> bool:
        power = self._rep(POWER_VS_HREF).get("x.com.samsung.da.power")
        if power is not None:
            return str(power).lower() == "on"
        return bool(self._rep(POWER_HREF).get("value"))

    def _label_for_code(self, code) -> str:
        """Lowercased HA preset label for a device mode code.

        The TP1X_DA-AC-AIR board (issue #130) reports named modes directly
        as supportedModes, so the code IS the label. The A-VTWW-TP2-21
        board (issue #151) instead reports numeric wind-strength codes with
        a separate modesName array giving the real names -- same shape as
        climate.py's _wind_strength_label, and coincidentally the same word
        set, so both generations land on identical HA preset values."""
        rep = self._mode_rep()
        supported = list(rep.get(_SUPPORTED_MODES_FIELD, ()))
        names = rep.get(_MODES_NAME_FIELD)
        if names and code in supported and len(names) == len(supported):
            return str(names[supported.index(code)]).lower()
        return str(code).lower()

    @property
    def preset_modes(self) -> list[str]:
        return [
            self._label_for_code(code) for code in self._mode_rep().get(_SUPPORTED_MODES_FIELD, ())
        ]

    @property
    def preset_mode(self) -> str | None:
        modes = self._mode_rep().get(_MODES_FIELD)
        code = modes[0] if isinstance(modes, (list, tuple)) and modes else modes
        return self._label_for_code(code) if code is not None else None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        await self.coordinator.async_send_command(self._bound, self._power_payload(True))
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, self._power_payload(False))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # Reverse-resolve against the unit's own supportedModes -- the
        # write needs the raw device code (e.g. 'WindFree', or '90' on the
        # modesName-labelled board), not the lowercased HA value.
        for code in self._mode_rep().get(_SUPPORTED_MODES_FIELD, ()):
            if self._label_for_code(code) == preset_mode:
                await self.coordinator.async_send_command(self._bound, ("mode", code))
                return
        _LOGGER.warning(
            "%s: %r is not a valid preset mode (supported: %s)",
            self.entity_id,
            preset_mode,
            self.preset_modes,
        )


_AIRFLOW_SPEED_FIELD = "speed"
# Raw `speed` codes, confirmed as Auto=0, Sleep=1, Low=2, Medium=3, High=4
# (air_purifier.py's module docstring). Auto and Sleep aren't speeds, so
# they are presets and only Low/Medium/High map to a percentage (#658).
_AIRFLOW_PRESET_CODES = {"auto": "0", "sleep": "1"}
_AIRFLOW_SPEED_CODES = ["2", "3", "4"]


class LocalThingsAirflowFan(LocalThingsEntity, FanEntity):
    """ARTIK051_TVTL-class air purifier fan (issue #56): Low/Medium/High as
    speeds, Auto and Sleep as presets, which HomeKit bridges as a speed
    slider plus one switch per preset."""

    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = len(_AIRFLOW_SPEED_CODES)

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None
        self._attr_preset_modes = list(_AIRFLOW_PRESET_CODES)

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _power_payload(self, enabled: bool) -> tuple[str, bool, str]:
        """Prefer /power/0 like LocalThingsRangeHoodFan above, NOT
        LocalThingsAirPurifierFan's vs/0-first order -- that order is only
        harmless for the TP1X board because it never reports /power/0.
        This family's dumps carry both hrefs, and common.POWER_GENERIC is
        unconditionally bound to /power/0 when present, so writing to
        /power/vs/0 first would leave power_switch and this fan
        disagreeing until the next poll."""
        resources = self._resources
        target = POWER_HREF if POWER_HREF in resources else POWER_VS_HREF
        return "power", enabled, target

    def _code(self) -> str:
        return str(self._rep(self._bound.href).get(_AIRFLOW_SPEED_FIELD, ""))

    @property
    def is_on(self) -> bool:
        power = self._rep(POWER_HREF)
        if "value" in power:
            return bool(power.get("value"))
        return str(self._rep(POWER_VS_HREF).get("x.com.samsung.da.power", "")).lower() == "on"

    @property
    def percentage(self) -> int | None:
        if not self.is_on:
            return 0
        current = self._code()
        if current not in _AIRFLOW_SPEED_CODES:
            return None
        return ordered_list_item_to_percentage(_AIRFLOW_SPEED_CODES, current)

    @property
    def preset_mode(self) -> str | None:
        if not self.is_on:
            return None
        current = self._code()
        return next((name for name, code in _AIRFLOW_PRESET_CODES.items() if code == current), None)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        elif self.preset_mode is not None:
            # HomeKit turns a preset switch off with a bare turn_on, so on
            # an already-running fan that means "leave the preset".
            await self._write_code(_AIRFLOW_SPEED_CODES[0])
        else:
            await self.coordinator.async_send_command(self._bound, self._power_payload(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, self._power_payload(False))

    async def _write_code(self, code: str) -> None:
        if not self.is_on:
            await self.coordinator.async_send_command(self._bound, self._power_payload(True))
        await self.coordinator.async_send_command(self._bound, ("speed", int(code)))

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        await self._write_code(percentage_to_ordered_list_item(_AIRFLOW_SPEED_CODES, percentage))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        code = _AIRFLOW_PRESET_CODES.get(preset_mode)
        if code is None:
            _LOGGER.warning(
                "%s: %r is not a valid preset mode (supported: %s)",
                self.entity_id,
                preset_mode,
                self.preset_modes,
            )
            return
        await self._write_code(code)

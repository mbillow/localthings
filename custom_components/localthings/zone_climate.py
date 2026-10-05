"""Climate entity for one space-heating zone of a Samsung EHS heat pump.

Registered by climate.py's platform setup. Like water_heater.py, it binds one
resource (the zone's temperature) and reads its siblings from the coordinator
snapshot; the descriptor names the zone's power, mode and temperature hrefs,
so each zone of a multi-zone unit (issue #581) is the same class. Mode is
device-wide on EHS, so changing one zone's HVAC mode changes every zone's.
"""

from __future__ import annotations

from typing import cast

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import UnitOfTemperature

from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity
from .registry.capabilities.common import normalize_temp_unit
from .registry.entities import ZoneClimateDesc

# The same mapping HA core's smartthings integration uses for EHS zones.
_DEVICE_TO_HVAC: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
}
_HVAC_TO_DEVICE: dict[HVACMode, str] = {
    HVACMode.COOL: "Cool",
    HVACMode.HEAT: "Heat",
    HVACMode.AUTO: "Auto",
}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


class LocalThingsZoneClimate(LocalThingsEntity, ClimateEntity):
    """One EHS zone: on/off, HVAC mode and the zone's setpoint."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._desc = cast(ZoneClimateDesc, bound.desc)

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(self._bound.subdevice.to_actual(href)) or {}

    def _is_on(self) -> bool:
        power = self._rep(self._desc.power_href).get("x.com.samsung.da.power", "")
        return str(power).lower() == "on"

    def _temperature(self, field: str):
        return _num(self._rep(self._desc.temperature_href).get(field))

    # -- temperature --------------------------------------------------------

    @property
    def temperature_unit(self) -> str:
        raw = self._rep(self._desc.temperature_href).get("x.com.samsung.da.unit")
        return (
            UnitOfTemperature.FAHRENHEIT
            if normalize_temp_unit(raw, "°C") == "°F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def current_temperature(self):
        return self._temperature("x.com.samsung.da.current")

    @property
    def target_temperature(self):
        return self._temperature("x.com.samsung.da.desired")

    @property
    def min_temp(self) -> float:
        lo = self._temperature("x.com.samsung.da.minimum")
        hi = self._temperature("x.com.samsung.da.maximum")
        return lo if lo is not None and hi is not None else super().min_temp

    @property
    def max_temp(self) -> float:
        lo = self._temperature("x.com.samsung.da.minimum")
        hi = self._temperature("x.com.samsung.da.maximum")
        return hi if lo is not None and hi is not None else super().max_temp

    @property
    def target_temperature_step(self) -> float:
        step = self._temperature("x.com.samsung.da.increment")
        return 0.5 if step is None else step

    # -- HVAC mode ------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self._is_on():
            return HVACMode.OFF
        code = _first(self._rep(self._desc.mode_href).get("x.com.samsung.da.modes"))
        return _DEVICE_TO_HVAC.get(str(code).lower()) if code is not None else None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF]
        for code in self._rep(self._desc.mode_href).get("x.com.samsung.da.supportedModes") or []:
            mode = _DEVICE_TO_HVAC.get(str(code).lower())
            if mode is not None and mode not in modes:
                modes.append(mode)
        return modes

    # -- writes ---------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        device = _HVAC_TO_DEVICE.get(hvac_mode)
        if device is None:
            return
        if not self._is_on():
            await self.async_turn_on()
        await self.coordinator.async_send_command(self._bound, ("mode", device))

    async def async_set_temperature(self, **kwargs) -> None:
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)
            if hvac_mode == HVACMode.OFF:
                return
        temp = kwargs.get("temperature")
        if temp is not None:
            await self.coordinator.async_send_command(self._bound, ("temperature", temp))

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", True))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", False))

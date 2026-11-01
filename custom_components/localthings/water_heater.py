"""Water heater platform for Local Things.

Second composite entity in this integration (see climate.py's module
docstring for the general pattern): a single HA water_heater card for a
Samsung EHS heat pump's domestic hot water (DHW) loop. It binds the primary
`WaterHeaterDesc` (the `/mode/dhw/vs/0` capability, DHW.entities in
registry/capabilities/ehs.py) and reads the sibling `/power/dhw/vs/0` and
`/temperatures/dhw/vs/0` resources straight from the coordinator snapshot,
the same cross-resource read climate.py uses.

Writes go through `coordinator.async_send_command`: DHW's `write_fn`
(ehs._dhw_write) maps each `(kind, value)` payload to the right
`(path_segs, body)`, applying the optimistic value/settle guard to that
resource's own href rather than the bound `/mode/dhw/vs/0` href.

Operation-mode vocabulary: the DHW loop's four device modes (Eco/Std/Force/
Power) map onto HA's own standard water_heater states, the same mapping
HA core's `smartthings` integration uses for this exact Samsung capability
(`samsungce.ehsThermostat`), just title-cased to match this OCF resource's
spelling. Reusing HA's standard states means no state translation catalog
entry is needed for them.

Naming differs from climate.py's: the AC *is* the device, so its card takes
the bare device name. An EHS unit has two loops, and DHW isn't "the
device" (siblings are named "Zone Mode"/"Zone Target Temperature"), so this
entity is named through the catalog via `translation_key='dhw'`
(entity.water_heater.dhw.name -> "Hot water").
"""

from __future__ import annotations

import logging

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities.common import normalize_temp_unit
from .registry.capabilities.ehs import (
    HREF_DHW_MODE as MODE_HREF,
)
from .registry.capabilities.ehs import (
    HREF_DHW_POWER as POWER_HREF,
)
from .registry.capabilities.ehs import (
    HREF_DHW_TEMPERATURE as TEMPERATURE_HREF,
)
from .registry.entities import WaterHeaterDesc

_LOGGER = logging.getLogger(__name__)

_MODES_FIELD = "x.com.samsung.da.modes"
_SUPPORTED_FIELD = "x.com.samsung.da.supportedModes"

# Device mode <-> HA water_heater operation state -- see module docstring.
_DEVICE_TO_STATE: dict[str, str] = {
    "Eco": STATE_ECO,
    "Std": STATE_HEAT_PUMP,
    "Force": STATE_HIGH_DEMAND,
    "Power": STATE_PERFORMANCE,
}
_STATE_TO_DEVICE = {v: k for k, v in _DEVICE_TO_STATE.items()}

# Read-side lookup, case-folded: this map is bijective (unlike climate.py's
# 'Wind'/'Fan' -> FAN_ONLY), so the write side uses _STATE_TO_DEVICE
# directly; only the read side needs to absorb a board spelling the same
# code differently ('eco'/'ECO').
_DEVICE_TO_STATE_CI = {k.lower(): v for k, v in _DEVICE_TO_STATE.items()}


def _to_state(code) -> str | None:
    if code is None:
        return None
    return _DEVICE_TO_STATE_CI.get(str(code).lower())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsWaterHeater(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, WaterHeaterDesc) and _is_included(b, coordinator)
    )


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(value):
    """Samsung `modes` is a single-element list on this resource. Return the
    first element of a list, else the value itself."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


class LocalThingsWaterHeater(LocalThingsEntity, WaterHeaterEntity):
    """Composite water_heater entity for a Samsung EHS DHW loop."""

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        # No _attr_name here: unlike climate.py's AC, this is one loop of a
        # two-loop device and takes a catalog name through translation_key.
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )
        # Raw device codes already logged by _warn_unmapped -- read on every
        # refresh, so un-deduped would spam the log for an unrecognized code.
        self._warned_unmapped: set[str] = set()

    def _rep(self, href: str) -> dict:
        """`href` is one of this module's canonical HREF_* constants,
        translated through this bound entity's own subdevice (issue #177),
        same as climate.py's identical helper."""
        return self.coordinator.resource(self._bound.subdevice.to_actual(href)) or {}

    def _is_on(self) -> bool:
        return str(self._rep(POWER_HREF).get("x.com.samsung.da.power", "")).lower() == "on"

    def _supported(self) -> list[str]:
        return list(self._rep(MODE_HREF).get(_SUPPORTED_FIELD) or [])

    def _warn_unmapped(self, code: str) -> None:
        if code in self._warned_unmapped:
            return
        self._warned_unmapped.add(code)
        _LOGGER.warning(
            "%s: device DHW mode %r has no HA mapping and was dropped; "
            "please file an issue with your diagnostics dump",
            self.entity_id,
            code,
        )

    # -- temperature --------------------------------------------------------

    @property
    def native_temperature_unit(self) -> str:
        raw = self._rep(TEMPERATURE_HREF).get("x.com.samsung.da.unit")
        return (
            UnitOfTemperature.FAHRENHEIT
            if normalize_temp_unit(raw, "°C") == "°F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def native_current_temperature(self):
        return _num(self._rep(TEMPERATURE_HREF).get("x.com.samsung.da.current"))

    @property
    def native_target_temperature(self):
        return _num(self._rep(TEMPERATURE_HREF).get("x.com.samsung.da.desired"))

    # Older cores need aliases; newer cores must not see deprecated members.
    if not hasattr(WaterHeaterEntity, "native_temperature_unit"):
        temperature_unit = native_temperature_unit
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    def _range(self) -> list | None:
        """The device's own (minimum, maximum) pair, or None. Both ends
        together or neither -- same rule as climate._range(); a board
        reporting only minimum would otherwise pair it with HA's own
        default maximum, silently wrong."""
        rep = self._rep(TEMPERATURE_HREF)
        lo = _num(rep.get("x.com.samsung.da.minimum"))
        hi = _num(rep.get("x.com.samsung.da.maximum"))
        return [lo, hi] if (lo is not None and hi is not None) else None

    @property
    def min_temp(self) -> float:
        r = self._range()
        return r[0] if r else super().min_temp

    @property
    def max_temp(self) -> float:
        r = self._range()
        return r[1] if r else super().max_temp

    @property
    def target_temperature_step(self) -> float:
        # `is None`, not `or` -- `or` would collapse a genuine 0 (issue #160).
        step = _num(self._rep(TEMPERATURE_HREF).get("x.com.samsung.da.increment"))
        return 0.5 if step is None else step

    # -- operation mode -------------------------------------------------------

    @property
    def current_operation(self) -> str | None:
        if not self._is_on():
            return STATE_OFF
        code = _first(self._rep(MODE_HREF).get(_MODES_FIELD))
        mapped = _to_state(code)
        if code is not None and mapped is None:
            self._warn_unmapped(code)
        return mapped

    @property
    def operation_list(self) -> list[str]:
        modes = [STATE_OFF]
        for code in self._supported():
            mapped = _to_state(code)
            if mapped is None:
                self._warn_unmapped(code)
                continue
            if mapped not in modes:
                modes.append(mapped)
        return modes

    # -- writes ---------------------------------------------------------------

    async def async_set_temperature(self, **kwargs) -> None:
        # HA's water_heater.set_temperature service can carry an optional
        # operation_mode; honor it, setting the mode first (which also
        # powers the loop on) so a dashboard "boost to 55" button that
        # carries a mode actually changes mode, not just the setpoint. Same
        # fix as climate.async_set_temperature.
        operation_mode = kwargs.get("operation_mode")
        if operation_mode is not None:
            await self.async_set_operation_mode(operation_mode)
            if operation_mode == STATE_OFF:
                return
        temp = kwargs.get("temperature")
        if temp is None:
            return
        await self.coordinator.async_send_command(self._bound, ("temperature", temp))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == STATE_OFF:
            await self.coordinator.async_send_command(self._bound, ("power", False))
            return
        device = _STATE_TO_DEVICE.get(operation_mode)
        if device is None:
            return
        if not self._is_on():
            await self.coordinator.async_send_command(self._bound, ("power", True))
        await self.coordinator.async_send_command(self._bound, ("mode", device))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", False))

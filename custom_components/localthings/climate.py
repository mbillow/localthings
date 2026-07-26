"""Climate platform for Local Things.

The first composite entity in this integration: a single HA climate card that
unifies several OCF resources of a Samsung air conditioner. Unlike every other
platform here (one descriptor -> one resource field), a climate entity reads
power, HVAC mode, current/target temperature, fan (wind) strength, swing (wind
direction) and the convenient-mode preset from *different* resources.

It binds one primary `BoundEntity` (the `/mode/vs/0` capability) so the registry
still tracks it, and reads the sibling resources straight from the coordinator
snapshot via `coordinator.resource(href)` -- the same cross-resource read that
`number.py` (live range/unit) and `select.py` (options callable) already do.

Writes go through `coordinator.async_send_command(bound, (kind, value))`: the
CLIMATE capability's `write_fn` maps each `(kind, value)` payload to the right
`(path_segs, body)`, and `async_send_command` POSTs to those path_segs and
applies the optimistic value/settle guard to that same href -- not the bound
`/mode/vs/0` href -- so one descriptor drives writes to, and gets fresh state
back for, power, mode, temperature and wind resources alike.
"""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    PRESET_NONE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import ClimateDesc
# The AC's canonical resource hrefs live in the capability module (the single
# source of truth shared with its COVERAGE caps); power prefers the OCF-standard
# href, falling back to the vendor one, mirroring common.POWER_GENERIC /
# POWER_VS_FALLBACK.
from .registry.capabilities.airconditioner import (
    HREF_MODE as MODE_HREF,
    HREF_POWER as POWER_HREF,
    HREF_POWER_VS as POWER_VS_HREF,
    HREF_TEMP_CURRENT as TEMP_CURRENT_HREF,
    HREF_TEMP_DESIRED as TEMP_DESIRED_HREF,
    HREF_TEMP_CONTROL as TEMP_CONTROL_HREF,
    HREF_TEMPS_VS as TEMPS_VS_HREF,
    HREF_WIND_STRENGTH as WIND_STRENGTH_HREF,
    HREF_WIND_DIRECTION as WIND_DIRECTION_HREF,
    HREF_CONVENIENT as CONVENIENT_HREF,
)
from .registry.capabilities.common import normalize_temp_unit

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included

_MODES_FIELD = 'x.com.samsung.da.modes'
_SUPPORTED_FIELD = 'x.com.samsung.da.supportedModes'

# --- device code <-> HA value maps -----------------------------------------
# HVAC mode: Samsung /mode/vs/0 modes <-> HA HVACMode (excluding OFF, which is
# driven by the power resource).
_DEVICE_TO_HVAC: dict[str, HVACMode] = {
    'Cool': HVACMode.COOL,
    'Dry': HVACMode.DRY,
    # Fan-only is spelled 'Wind' on some boards (e.g. TP1X_DA-AC-RAC-01001) and
    # 'Fan' on others (e.g. TP1X_DA-AC-RAC-01011); both map to FAN_ONLY. The
    # reverse write can't rely on this map alone (two codes, one HA value) --
    # _device_code_for_hvac() resolves the code from the unit's supportedModes.
    'Wind': HVACMode.FAN_ONLY,
    'Fan': HVACMode.FAN_ONLY,
    # The device's 'Auto' is a single-setpoint "device decides" mode -> HA
    # HVACMode.AUTO (renders "Auto"). Not HEAT_COOL: that renders "Heat/cool"
    # and implies a two-setpoint heat+cool range these single-setpoint units
    # (including cool-only models) don't have.
    'Auto': HVACMode.AUTO,
    'Heat': HVACMode.HEAT,
}
_HVAC_TO_DEVICE = {v: k for k, v in _DEVICE_TO_HVAC.items()}

# Fan (wind strength): device codes "0".."4" -> HA standard fan constants where
# a clean match exists so they auto-localize; "turbo" is custom (translated).
_DEVICE_TO_FAN: dict[str, str] = {
    '0': 'auto',
    '1': 'low',
    '2': 'medium',
    '3': 'high',
    '4': 'turbo',
}
_FAN_TO_DEVICE = {v: k for k, v in _DEVICE_TO_FAN.items()}

# Swing (wind direction): all map onto HA standard swing constants (auto-localize).
_DEVICE_TO_SWING: dict[str, str] = {
    'Fix': 'off',
    'All': 'both',
    'Up_And_Low': 'vertical',
}
_SWING_TO_DEVICE = {v: k for k, v in _DEVICE_TO_SWING.items()}

# Preset (convenient mode): resolved dynamically from the device's own
# /mode/convenient/vs/0 supportedModes -- no per-model table. The device 'Off'
# code maps to HA's PRESET_NONE ("no preset active"); every other code is exposed
# as its lowercased self and labelled in translations
# (entity.climate.airconditioner.state_attributes.preset_mode.state.<code>), so
# any board's convenient modes surface without code changes and an unlabelled
# code renders as its raw value until a label is added. (Samsung's WindFree
# still-air cooling shows up here as the 'Nano'/'NanoSleep' codes on cool-only
# global RAC boards -- that's just a translation label, not a hard-coded mode.)
def _preset_to_ha(code) -> str:
    return PRESET_NONE if code == 'Off' else str(code).lower()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsClimate(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, ClimateDesc) and _is_included(b, coordinator)
    )


def _first(value):
    """Samsung `modes` is a single-element list on some resources, a scalar on
    others. Return the first element of a list, else the value itself."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _temps_vs_item(rep: dict) -> dict:
    """First item of the vendor `/temperatures/vs/0` items[] array.

    Newer AC firmware (Tizen Lite, oneUiVersion "7.0 Air conditioner", e.g.
    model TP1X_DA-AC-RAC-01011) does NOT expose the OCF-standard
    /temperature/current/0 + /temperature/desired/0 pair; it reports current
    and target under a single `/temperatures/vs/0` resource whose
    `x.com.samsung.da.items[0]` carries current/desired/minimum/maximum/
    increment/unit. Returns {} when absent, so callers fall through cleanly.
    """
    items = rep.get('x.com.samsung.da.items')
    if isinstance(items, (list, tuple)) and items and isinstance(items[0], dict):
        return items[0]
    return {}


class LocalThingsClimate(LocalThingsEntity, ClimateEntity):
    """Composite climate entity for a Samsung air conditioner."""

    # translation_key comes from the ClimateDesc (base __init__ sets
    # _attr_translation_key from bound.desc), resolving the state_attributes
    # translations under entity.climate.airconditioner.
    # Modern climate entities opt out of the deprecated auto-added TURN_ON/OFF.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        # Primary/main entity for the device: no name suffix, just the device name.
        self._attr_name = None
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    # -- resource helpers ---------------------------------------------------

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _is_on(self) -> bool:
        # Prefer the vendor /power/vs/0 (present on every observed board and the
        # resource writes target -- see airconditioner._climate_write). The
        # OCF /power/0 is absent on many boards and a stale mirror on some, so
        # reading it first showed pre-write state after a power toggle.
        vs = self._rep(POWER_VS_HREF)
        power = vs.get('x.com.samsung.da.power')
        if power is not None:
            return str(power).lower() == 'on'
        return bool(self._rep(POWER_HREF).get('value'))

    def _supported(self, href: str) -> list[str]:
        return list(self._rep(href).get(_SUPPORTED_FIELD) or [])

    def _read_mode(self, href: str, mapping: dict):
        """Current mode of a wind/convenient resource, mapped to its HA value."""
        return mapping.get(_first(self._rep(href).get(_MODES_FIELD)))

    def _read_modes(self, href: str, mapping: dict) -> list[str]:
        """Supported modes of a resource, mapped to HA values (unknowns dropped)."""
        return [mapping[c] for c in self._supported(href) if c in mapping]

    # -- temperature --------------------------------------------------------

    def _ocf_temp_authoritative(self) -> bool:
        """True when the OCF /temperature/{current,desired}/0 pair is the
        authoritative temperature channel -- signalled by /temperature/current/0
        being present. Those boards honour reads/writes on /temperature/desired/0
        and reject the vendor /temperatures/vs/0; boards without the pair (only a
        desired stub, or nothing) are the reverse. Confirmed on live units of
        both kinds."""
        return bool(self._rep(TEMP_CURRENT_HREF))

    def _temps_vs(self) -> dict:
        """Vendor `/temperatures/vs/0` items[0] (empty {} when absent)."""
        return _temps_vs_item(self._rep(TEMPS_VS_HREF))

    @property
    def temperature_unit(self) -> str:
        raw = self._rep(TEMP_DESIRED_HREF).get('units')
        if raw is None:
            raw = self._temps_vs().get('x.com.samsung.da.unit')
        return (UnitOfTemperature.FAHRENHEIT
                if normalize_temp_unit(raw, '°C') == '°F'
                else UnitOfTemperature.CELSIUS)

    @property
    def current_temperature(self):
        v = _num(self._rep(TEMP_CURRENT_HREF).get('temperature'))
        if v is None:
            v = _num(self._temps_vs().get('x.com.samsung.da.current'))
        return v

    @property
    def target_temperature(self):
        # Read from the same channel writes go to (see async_set_temperature):
        # OCF /temperature/desired/0 on boards with the full OCF pair, vendor
        # /temperatures/vs/0 otherwise -- with the other as fallback.
        ocf = _num(self._rep(TEMP_DESIRED_HREF).get('temperature'))
        vs = _num(self._temps_vs().get('x.com.samsung.da.desired'))
        if self._ocf_temp_authoritative():
            return ocf if ocf is not None else vs
        return vs if vs is not None else ocf

    def _range(self) -> list | None:
        r = self._rep(TEMP_DESIRED_HREF).get('range')
        if isinstance(r, (list, tuple)) and len(r) == 2:
            return r
        item = self._temps_vs()
        lo = _num(item.get('x.com.samsung.da.minimum'))
        hi = _num(item.get('x.com.samsung.da.maximum'))
        return [lo, hi] if (lo is not None and hi is not None) else None

    @property
    def min_temp(self) -> float:
        r = self._range()
        return float(r[0]) if r else super().min_temp

    @property
    def max_temp(self) -> float:
        r = self._range()
        return float(r[1]) if r else super().max_temp

    @property
    def target_temperature_step(self) -> float:
        return (_num(self._rep(TEMP_CONTROL_HREF).get('increment'))
                or _num(self._rep(TEMP_CONTROL_HREF).get('x.com.samsung.da.increment'))
                or _num(self._temps_vs().get('x.com.samsung.da.increment'))
                or 1.0)

    # -- hvac mode ----------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._is_on():
            return HVACMode.OFF
        device = _first(self._rep(MODE_HREF).get(_MODES_FIELD))
        return _DEVICE_TO_HVAC.get(device, HVACMode.AUTO)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF]
        for m in self._supported(MODE_HREF):
            mapped = _DEVICE_TO_HVAC.get(m)
            if mapped is not None and mapped not in modes:
                modes.append(mapped)
        return modes

    # -- fan / swing / preset ----------------------------------------------

    @property
    def fan_mode(self):
        return self._read_mode(WIND_STRENGTH_HREF, _DEVICE_TO_FAN)

    @property
    def fan_modes(self) -> list[str]:
        return self._read_modes(WIND_STRENGTH_HREF, _DEVICE_TO_FAN)

    @property
    def swing_mode(self):
        return self._read_mode(WIND_DIRECTION_HREF, _DEVICE_TO_SWING)

    @property
    def swing_modes(self) -> list[str]:
        return self._read_modes(WIND_DIRECTION_HREF, _DEVICE_TO_SWING)

    @property
    def preset_mode(self):
        code = _first(self._rep(CONVENIENT_HREF).get(_MODES_FIELD))
        return _preset_to_ha(code) if code is not None else None

    @property
    def preset_modes(self) -> list[str]:
        return [_preset_to_ha(c) for c in self._supported(CONVENIENT_HREF)]

    # -- writes -------------------------------------------------------------

    def _device_code_for_hvac(self, hvac_mode: HVACMode):
        """Device mode code for an HA hvac_mode, chosen from this unit's own
        supportedModes -- fan-only is 'Wind' on some boards and 'Fan' on
        others, so the reverse map alone can't pick the code this unit accepts.
        """
        for code in self._supported(MODE_HREF):
            if _DEVICE_TO_HVAC.get(code) == hvac_mode:
                return code
        return _HVAC_TO_DEVICE.get(hvac_mode)

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get('temperature')
        if temp is None:
            return
        if self._ocf_temp_authoritative():
            # OCF-pair boards apply the write on /temperature/desired/0.
            await self.coordinator.async_send_command(
                self._bound, ('temperature_ocf', temp))
        else:
            # Vendor boards apply it on /temperatures/vs/0; pass the current
            # item so the write RMWs it (preserving current/min/max/unit).
            await self.coordinator.async_send_command(
                self._bound, ('temperature', temp, self._temps_vs()))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send_command(self._bound, ('power', False))
            return
        device = self._device_code_for_hvac(hvac_mode)
        if device is None:
            return
        if not self._is_on():
            await self.coordinator.async_send_command(self._bound, ('power', True))
        await self.coordinator.async_send_command(self._bound, ('mode', device))

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send_command(self._bound, ('power', True))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send_command(self._bound, ('power', False))

    async def _set_mapped(self, kind: str, mapping: dict, value: str) -> None:
        """Map an HA fan/swing/preset value back to its device code and write it."""
        device = mapping.get(value)
        if device is not None:
            await self.coordinator.async_send_command(self._bound, (kind, device))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._set_mapped('fan', _FAN_TO_DEVICE, fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self._set_mapped('swing', _SWING_TO_DEVICE, swing_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # Reverse-resolve against the unit's own supportedModes (codes aren't a
        # fixed transform of the HA value -- e.g. 'NanoSleep' -> 'nanosleep').
        for code in self._supported(CONVENIENT_HREF):
            if _preset_to_ha(code) == preset_mode:
                await self.coordinator.async_send_command(self._bound, ('preset', code))
                return

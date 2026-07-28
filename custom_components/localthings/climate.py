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

import logging

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
    HREF_WIND_OSCILLATION as WIND_OSCILLATION_HREF,
    HREF_CONVENIENT as CONVENIENT_HREF,
)
from .registry.capabilities.common import normalize_temp_unit

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included

_LOGGER = logging.getLogger(__name__)

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
    # _device_code_for_hvac() resolves the code from the unit's own
    # supportedModes, so this is only a fallback for a unit reporting no
    # supportedModes at all. 'Fan' is listed first so the {v: k} reverse
    # comprehension below has 'Wind' win that fallback (last-key-wins),
    # preserving the original single-spelling behavior rather than silently
    # flipping it when 'Fan' was added.
    'Fan': HVACMode.FAN_ONLY,
    'Wind': HVACMode.FAN_ONLY,
    # The device's 'Auto' is a single-setpoint "device decides" mode -> HA
    # HVACMode.AUTO (renders "Auto"). Not HEAT_COOL: that renders "Heat/cool"
    # and implies a two-setpoint heat+cool range these single-setpoint units
    # (including cool-only models) don't have.
    'Auto': HVACMode.AUTO,
    'Heat': HVACMode.HEAT,
}
_HVAC_TO_DEVICE = {v: k for k, v in _DEVICE_TO_HVAC.items()}

# AI-driven auto-comfort mode (issue #93, A-CAWW-TP2-20-COMMON). Not a flat
# _DEVICE_TO_HVAC entry: 'AIComfort' isn't a distinct thermodynamic operation
# like Cool/Dry/Heat, it's an AI overlay on top of the device's own 'Auto'
# behavior -- confirmed by this unit reporting both 'Auto' and 'AIComfort' as
# separate, mutually-exclusive entries in /mode/vs/0's supportedModes. Modeled
# the idiomatic HA way instead: hvac_mode reports AUTO (same as the plain
# 'Auto' code maps to) and a dedicated 'ai_comfort' preset carries the
# distinction a bare hvac_mode can't. Not reachable via async_set_hvac_mode --
# entered/left only through the preset, since there's no dedicated HVACMode
# value for it to write back to.
_AI_COMFORT_MODE = 'AIComfort'
PRESET_AI_COMFORT = 'ai_comfort'

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
    'Left_And_Right': 'horizontal',  # issue #75
}
_SWING_TO_DEVICE = {v: k for k, v in _DEVICE_TO_SWING.items()}

# Swing fallback via /wind/oscillation/vs/0 (issue #126) -- boards without
# WIND_DIRECTION_HREF at all report two independent Swing|Fix toggles
# instead of one combined code. Same HA vocabulary as _DEVICE_TO_SWING
# above (off/vertical/horizontal/both), just read from/written to a pair
# of fields rather than a single one.
def _oscillation_swing(rep: dict) -> str | None:
    vertical = rep.get('vertical')
    horizontal = rep.get('horizontal')
    if vertical is None and horizontal is None:
        return None
    v = vertical == 'Swing'
    h = horizontal == 'Swing'
    if v and h:
        return 'both'
    if v:
        return 'vertical'
    if h:
        return 'horizontal'
    return 'off'

# Preset (convenient mode): resolved dynamically from the device's own
# /mode/convenient/vs/0 supportedModes -- no per-model table. The device 'Off'
# code maps to HA's PRESET_NONE ("no preset active"); every other code is
# exposed as its lowercased self and labelled in translations
# (entity.climate.airconditioner.state_attributes.preset_mode.state.<code>),
# so any board's convenient modes surface without code changes, and an
# unlabelled code just renders as its raw value until a label is added.
# (Samsung's WindFree still-air cooling shows up here as the 'Nano'/
# 'NanoSleep' codes on cool-only global RAC boards -- that's just a
# translation label, not a hard-coded mode.)
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
        # (href, raw device code) pairs already logged by _warn_unmapped --
        # these properties are read on every coordinator refresh, so an
        # un-deduped warning would spam the log for any device with a
        # genuinely unrecognized code.
        self._warned_unmapped: set[tuple[str, str]] = set()

    # -- resource helpers ---------------------------------------------------

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href) or {}

    def _is_on(self) -> bool:
        # Prefer the vendor /power/vs/0 (present on every observed board and
        # the resource writes target -- see airconditioner._climate_write).
        # The OCF /power/0 is absent on many boards and a stale mirror on
        # some, so reading it first showed pre-write state after a power
        # toggle (issue #53: "can turn on but not off").
        power = self._rep(POWER_VS_HREF).get('x.com.samsung.da.power')
        if power is not None:
            return str(power).lower() == 'on'
        return bool(self._rep(POWER_HREF).get('value'))

    def _supported(self, href: str) -> list[str]:
        return list(self._rep(href).get(_SUPPORTED_FIELD) or [])

    def _warn_unmapped(self, href: str, code: str) -> None:
        """Log once per (href, code) when a device-reported mode has no
        entry in the relevant device<->HA map, so a real device gap surfaces
        in the log instead of silently vanishing (issue #93)."""
        key = (href, code)
        if key in self._warned_unmapped:
            return
        self._warned_unmapped.add(key)
        _LOGGER.warning(
            "%s: device mode %r on %s has no HA mapping and was dropped; "
            "please file an issue with your diagnostics dump",
            self.entity_id, code, href,
        )

    def _read_mode(self, href: str, mapping: dict):
        """Current mode of a wind/convenient resource, mapped to its HA value."""
        raw = _first(self._rep(href).get(_MODES_FIELD))
        if raw is not None and raw not in mapping:
            self._warn_unmapped(href, raw)
        return mapping.get(raw)

    def _read_modes(self, href: str, mapping: dict) -> list[str]:
        """Supported modes of a resource, mapped to HA values (unknowns dropped)."""
        supported = self._supported(href)
        for c in supported:
            if c not in mapping:
                self._warn_unmapped(href, c)
        return [mapping[c] for c in supported if c in mapping]

    # -- temperature --------------------------------------------------------

    def _ocf_temp_authoritative(self) -> bool:
        """True when the OCF /temperature/{current,desired}/0 pair is the
        authoritative temperature channel -- signalled by
        /temperature/current/0 being present. Those boards honour reads/
        writes on /temperature/desired/0 and ignore the vendor
        /temperatures/vs/0; boards without the pair (only a desired stub, or
        nothing) are the reverse. Confirmed on live units of both kinds."""
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
        if device == _AI_COMFORT_MODE:
            return HVACMode.AUTO
        if device is not None and device not in _DEVICE_TO_HVAC:
            self._warn_unmapped(MODE_HREF, device)
        return _DEVICE_TO_HVAC.get(device, HVACMode.AUTO)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF]
        for m in self._supported(MODE_HREF):
            if m == _AI_COMFORT_MODE:
                continue
            mapped = _DEVICE_TO_HVAC.get(m)
            if mapped is None:
                self._warn_unmapped(MODE_HREF, m)
                continue
            if mapped not in modes:
                modes.append(mapped)
        return modes

    # -- fan / swing / preset ----------------------------------------------

    @property
    def fan_mode(self):
        return self._read_mode(WIND_STRENGTH_HREF, _DEVICE_TO_FAN)

    @property
    def fan_modes(self) -> list[str]:
        return self._read_modes(WIND_STRENGTH_HREF, _DEVICE_TO_FAN)

    def _swing_via_direction(self) -> bool:
        """True when WIND_DIRECTION_HREF is the swing channel to use --
        signalled by its presence. Boards without it (issue #126) report
        the 2-axis oscillation resource instead; see _oscillation_swing."""
        return bool(self._rep(WIND_DIRECTION_HREF))

    @property
    def swing_mode(self):
        if self._swing_via_direction():
            return self._read_mode(WIND_DIRECTION_HREF, _DEVICE_TO_SWING)
        return _oscillation_swing(self._rep(WIND_OSCILLATION_HREF))

    @property
    def swing_modes(self) -> list[str]:
        if self._swing_via_direction():
            return self._read_modes(WIND_DIRECTION_HREF, _DEVICE_TO_SWING)
        if self._rep(WIND_OSCILLATION_HREF):
            return list(_SWING_TO_DEVICE.keys())
        return []

    @property
    def preset_mode(self):
        if _first(self._rep(MODE_HREF).get(_MODES_FIELD)) == _AI_COMFORT_MODE:
            return PRESET_AI_COMFORT
        code = _first(self._rep(CONVENIENT_HREF).get(_MODES_FIELD))
        return _preset_to_ha(code) if code is not None else None

    @property
    def preset_modes(self) -> list[str]:
        modes = [_preset_to_ha(c) for c in self._supported(CONVENIENT_HREF)]
        if _AI_COMFORT_MODE in self._supported(MODE_HREF):
            modes.append(PRESET_AI_COMFORT)
        return modes

    # -- writes -------------------------------------------------------------

    def _device_code_for_hvac(self, hvac_mode: HVACMode):
        """Device mode code for an HA hvac_mode, chosen from this unit's own
        supportedModes -- fan-only is 'Wind' on some boards and 'Fan' on
        others, so the reverse map alone can't pick the code this unit
        accepts."""
        for code in self._supported(MODE_HREF):
            if _DEVICE_TO_HVAC.get(code) == hvac_mode:
                return code
        return _HVAC_TO_DEVICE.get(hvac_mode)

    async def async_set_temperature(self, **kwargs) -> None:
        # HA's set_temperature service forwards an optional hvac_mode here; honour
        # it (set the mode first -- that also powers the unit on when it was off),
        # matching the climate contract other integrations follow. Without this a
        # set_temperature call carrying hvac_mode (e.g. a dashboard "turn on to
        # Auto 24" button) set the setpoint but never changed mode or powered on.
        temp = kwargs.get('temperature')
        temperature_command = None
        if temp is not None:
            kind = (
                'temperature_ocf'
                if self._ocf_temp_authoritative()
                else 'temperature'
            )
            temperature_command = (kind, temp)
        hvac_mode = kwargs.get('hvac_mode')
        if hvac_mode is not None:
            await self._async_set_hvac_mode(
                hvac_mode, preserve_target=temp is None)
            if hvac_mode == HVACMode.OFF:
                return
        if temperature_command is None:
            return
        # OCF-pair boards write /temperature/desired/0; vendor boards write
        # /temperatures/vs/0 (see airconditioner._climate_write). The channel
        # is captured before a requested mode transition because its refresh
        # may temporarily expose an incomplete resource snapshot.
        await self.coordinator.async_send_command(
            self._bound, temperature_command)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._async_set_hvac_mode(hvac_mode, preserve_target=True)

    async def _async_set_hvac_mode(
        self, hvac_mode: HVACMode, *, preserve_target: bool,
    ) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send_command(self._bound, ('power', False))
            return
        device = self._device_code_for_hvac(hvac_mode)
        if device is None:
            return
        was_off = not self._is_on()
        preserved_command = None
        if was_off and preserve_target:
            target = self.target_temperature
            if target is not None:
                kind = (
                    'temperature_ocf'
                    if self._ocf_temp_authoritative()
                    else 'temperature'
                )
                preserved_command = (kind, target)
        if was_off:
            await self.coordinator.async_send_command(self._bound, ('power', True))
        await self.coordinator.async_send_command(self._bound, ('mode', device))
        # Some boards restore a stale secondary temperature channel while
        # powering up. Reassert the pre-transition setpoint after issuing the
        # power and mode writes so that OFF -> ON keeps the user's existing
        # target (including a valid minimum such as 16 °C).
        if preserved_command is not None:
            await self.coordinator.async_send_command(
                self._bound, preserved_command)

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
        if self._swing_via_direction():
            await self._set_mapped('swing', _SWING_TO_DEVICE, swing_mode)
            return
        if self._rep(WIND_OSCILLATION_HREF):
            await self.coordinator.async_send_command(
                self._bound, ('oscillation', swing_mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_AI_COMFORT:
            # Writes the primary mode resource, not the convenient one --
            # 'AIComfort' lives in /mode/vs/0 alongside Cool/Dry/Auto, not in
            # /mode/convenient/vs/0 with Quiet/Smart/Speed/Sleep.
            await self.coordinator.async_send_command(self._bound, ('mode', _AI_COMFORT_MODE))
            return
        # Reverse-resolve against the unit's own supportedModes (codes aren't
        # a fixed transform of the HA value -- e.g. 'NanoSleep' -> 'nanosleep').
        for code in self._supported(CONVENIENT_HREF):
            if _preset_to_ha(code) == preset_mode:
                await self.coordinator.async_send_command(self._bound, ('preset', code))
                return

"""Climate platform for Local Things.

The first composite entity in this integration: a single HA climate card
that unifies several OCF resources of a Samsung air conditioner. Unlike
every other platform here (one descriptor -> one resource field), a climate
entity reads power, HVAC mode, current/target temperature, fan (wind)
strength, swing (wind direction) and the convenient-mode preset from
*different* resources.

It binds one primary `BoundEntity` (the `/mode/vs/0` capability) so the
registry still tracks it, and reads the sibling resources straight from the
coordinator snapshot via `coordinator.resource(href)`.

Writes go through `coordinator.async_send_command(bound, (kind, value))`:
the CLIMATE capability's `write_fn` maps each `(kind, value)` payload to the
right `(path_segs, body)`, and `async_send_command` applies the optimistic
value/settle guard to that resource's own href -- not the bound
`/mode/vs/0` href -- so one descriptor drives writes across power, mode,
temperature and wind resources alike.
"""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities.airconditioner import (
    HREF_AIRFLOW as AIRFLOW_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_CONVENIENT as CONVENIENT_HREF,
)

# The AC's canonical resource hrefs live in the capability module (the single
# source of truth shared with its COVERAGE caps); power prefers the OCF-standard
# href, falling back to the vendor one, mirroring common.POWER_GENERIC /
# POWER_VS_FALLBACK.
from .registry.capabilities.airconditioner import (
    HREF_MODE as MODE_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_POWER as POWER_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_POWER_VS as POWER_VS_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_TEMP_CONTROL as TEMP_CONTROL_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_TEMP_CURRENT as TEMP_CURRENT_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_TEMP_DESIRED as TEMP_DESIRED_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_TEMPS_VS as TEMPS_VS_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_WIND_DIRECTION as WIND_DIRECTION_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_WIND_OSCILLATION as WIND_OSCILLATION_HREF,
)
from .registry.capabilities.airconditioner import (
    HREF_WIND_STRENGTH as WIND_STRENGTH_HREF,
)
from .registry.capabilities.airconditioner import (
    is_legacy_board,
)
from .registry.capabilities.common import normalize_temp_unit
from .registry.entities import ClimateDesc

_LOGGER = logging.getLogger(__name__)

_MODES_FIELD = "x.com.samsung.da.modes"
_SUPPORTED_FIELD = "x.com.samsung.da.supportedModes"

# --- device code <-> HA value maps -----------------------------------------
# HVAC mode: Samsung /mode/vs/0 modes <-> HA HVACMode (excluding OFF, which is
# driven by the power resource).
_DEVICE_TO_HVAC: dict[str, HVACMode] = {
    "Cool": HVACMode.COOL,
    "Dry": HVACMode.DRY,
    # Fan-only is spelled 'Wind' on some boards and 'Fan' on others; both map
    # to FAN_ONLY. _device_code_for_hvac() resolves the write-side code from
    # the unit's own supportedModes, so this reverse map is only a fallback
    # for a unit with no supportedModes at all. 'Fan' listed first so the
    # {v: k} comprehension below has 'Wind' win that fallback (last-key-wins,
    # preserving the original single-spelling behavior).
    "Fan": HVACMode.FAN_ONLY,
    "Wind": HVACMode.FAN_ONLY,
    # A single-setpoint "device decides" mode -> HA AUTO, not HEAT_COOL
    # (which implies a two-setpoint heat+cool range these units don't have).
    "Auto": HVACMode.AUTO,
    "Heat": HVACMode.HEAT,
}
_HVAC_TO_DEVICE = {v: k for k, v in _DEVICE_TO_HVAC.items()}

# AI-driven auto-comfort mode (issue #93, A-CAWW-TP2-20-COMMON): 'AIComfort'
# isn't a distinct thermodynamic operation like Cool/Dry/Heat, it's an AI
# overlay on the device's own 'Auto' -- the unit reports both as separate,
# mutually-exclusive supportedModes entries. hvac_mode reports AUTO (same as
# plain 'Auto') and a dedicated 'ai_comfort' preset carries the distinction.
# Not reachable via async_set_hvac_mode -- entered/left only through the
# preset, since there's no HVACMode value for it to write back to.
_AI_COMFORT_MODE = "AIComfort"
PRESET_AI_COMFORT = "ai_comfort"

# Codes in /mode/vs/0's supportedModes that are option/capability flags, not
# selectable thermodynamic operations -- dropped silently rather than
# tripping the issue #93 unmapped-code warning on every start.
#
# HOMECARE_WIZARD_V2 (issue #235, TP2X_RAC_20K) also appears in
# /configuration/vs/0's airconOptionList alongside other capability flags,
# and the unit's own current `modes` never reported it active -- consistent
# with an echoed capability flag, not a genuine mode. Unlike _AI_COMFORT_MODE,
# not modeled as a preset either: nothing confirms it's user-selectable.
_NON_HVAC_OPTION_CODES = frozenset({"HOMECARE_WIZARD_V2"})

# Fan (wind strength): device codes "0".."4" -> HA standard fan constants where
# a clean match exists so they auto-localize; "turbo" is custom (translated).
_DEVICE_TO_FAN: dict[str, str] = {
    "0": "auto",
    "1": "low",
    "2": "medium",
    "3": "high",
    "4": "turbo",
}
_FAN_TO_DEVICE = {v: k for k, v in _DEVICE_TO_FAN.items()}

# Swing (wind direction): all map onto HA standard swing constants (auto-localize).
_DEVICE_TO_SWING: dict[str, str] = {
    "Fix": "off",
    "All": "both",
    "Up_And_Low": "vertical",
    "Left_And_Right": "horizontal",  # issue #75
}
_SWING_TO_DEVICE = {v: k for k, v in _DEVICE_TO_SWING.items()}


# Swing fallback via /wind/oscillation/vs/0 (issue #126): boards without
# WIND_DIRECTION_HREF report two independent Swing|Fix toggles instead of
# one combined code. Same HA vocabulary as _DEVICE_TO_SWING above.
def _oscillation_swing(rep: dict) -> str | None:
    vertical = rep.get("vertical")
    horizontal = rep.get("horizontal")
    if vertical is None and horizontal is None:
        return None
    v = vertical == "Swing"
    h = horizontal == "Swing"
    if v and h:
        return "both"
    if v:
        return "vertical"
    if h:
        return "horizontal"
    return "off"


def _wind_strength_label(code, rep: dict) -> str:
    """Human label for a /wind/strength/vs/0 code from the device's own
    modesName array (parallel-indexed with supportedModes), lowercased --
    used only for codes _DEVICE_TO_FAN doesn't already cover (issue #155:
    a board using codes "0"/"31"-"35" instead of the "0"-"4" scale
    _DEVICE_TO_FAN was built from, with modesName giving the real labels).
    Falls back to the raw code lowercased when modesName is absent or
    misaligned."""
    supported = rep.get("x.com.samsung.da.supportedModes") or []
    names = rep.get("x.com.samsung.da.modesName") or []
    if code in supported and len(names) == len(supported):
        return str(names[supported.index(code)]).lower()
    return str(code).lower()


# Preset (convenient mode): resolved dynamically from the device's own
# /mode/convenient/vs/0 supportedModes -- no per-model table. Device 'Off'
# maps to PRESET_NONE; every other code is exposed lowercased and labelled
# in translations, so any board's convenient modes surface without code
# changes, and an unlabelled code renders as its raw value.
def _preset_to_ha(code) -> str:
    return PRESET_NONE if code == "Off" else str(code).lower()


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

    Newer AC firmware (Tizen Lite) doesn't expose the OCF-standard
    /temperature/current/0 + /temperature/desired/0 pair; it packs current/
    desired/minimum/maximum/increment/unit into this one resource's
    items[0] instead. Returns {} when absent, so callers fall through
    cleanly.
    """
    items = rep.get("x.com.samsung.da.items")
    if isinstance(items, (list, tuple)) and items and isinstance(items[0], dict):
        return items[0]
    return {}


class LocalThingsClimate(LocalThingsEntity, ClimateEntity):
    """Composite climate entity for a Samsung air conditioner."""

    # Opts out of the deprecated auto-added TURN_ON/OFF backwards compat.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._attr_name = None  # primary entity: no name suffix
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        # (href, raw device code) pairs already logged by _warn_unmapped --
        # these properties are read on every refresh, so an un-deduped
        # warning would spam the log for a genuinely unrecognized code.
        self._warned_unmapped: set[tuple[str, str]] = set()

    # -- resource helpers ---------------------------------------------------

    # Preset codes on legacy ARTIK051 boards, learned by driving the same unit
    # through its cloud integration and reading the local token back each time:
    # Nano=windFree, Quiet, Comfort, 2Step, Speed=Fast Turbo, Off=none.
    #
    # Smart is Samsung's "Smart Saver", which has no cloud counterpart to learn
    # it from -- it was read out of the appliance's own app and then confirmed
    # on hardware: writing Comode_Smart moved the unit's reported min_temp from
    # 16 to 24 and lifted a 22 target to 24, which is exactly what Samsung
    # documents Smart Saver doing, and Comode_Off put both back.
    _LEGACY_PRESET_CODES = ("Off", "Nano", "Quiet", "Comfort", "2Step", "Speed", "Smart")

    def _legacy_convenient(self) -> dict:
        """A /mode/convenient/vs/0-shaped rep built from the Comode_* token in
        /mode/vs/0's options, for boards that have no convenient resource."""
        options = self._rep(MODE_HREF).get("x.com.samsung.da.options") or []
        for option in options:
            if isinstance(option, str) and option.startswith("Comode_"):
                return {
                    _MODES_FIELD: [option.split("_", 1)[1]],
                    _SUPPORTED_FIELD: list(self._LEGACY_PRESET_CODES),
                }
        return {}

    def _legacy_airflow(self) -> dict:
        """The /airflow/vs/0 rep, but only when it is the fan/swing channel
        to use -- i.e. this board has no /wind/strength/vs/0.

        Delegates the board-generation test to is_legacy_board (the same
        test the token entities in capabilities/airconditioner.py use)
        instead of re-implementing it, using self._resources (issue #177)
        rather than a presence dict built from coordinator.resource()'s
        truthiness -- resource() collapses "href absent" and "href present
        but empty" to the same falsy value, while is_legacy_board tests key
        membership. Reads through self._rep, not coordinator.resource()
        directly, so a subdevice's own /airflow/vs/1 gets translated first,
        like every other sibling read below.
        """
        if not is_legacy_board(self._resources):
            return {}
        return self._rep(AIRFLOW_HREF)

    def _legacy_preset(self) -> bool:
        """Whether presets come from the Comode_* token rather than a
        resource. Gated on the same board test as _legacy_airflow, not on
        the convenient rep being empty alone: newer boards carry Comode
        tokens too, so a momentarily empty /mode/convenient/vs/0 must not
        silently switch the preset path over.

        Reads the raw href directly rather than through self._rep's own
        CONVENIENT_HREF fallback -- that fallback IS the legacy_convenient()
        rep this method is deciding whether to use, so routing through it
        would make the resource never look empty.
        """
        convenient_href = self._bound.subdevice.to_actual(CONVENIENT_HREF)
        return not self.coordinator.resource(convenient_href) and bool(self._legacy_airflow())

    def _rep(self, href: str) -> dict:
        """`href` is one of this module's canonical HREF_* constants,
        translated through this bound entity's own subdevice (issue #177)
        to the real on-the-wire href -- identity for MAIN."""
        rep = self.coordinator.resource(self._bound.subdevice.to_actual(href)) or {}
        if not rep and href == CONVENIENT_HREF and self._legacy_airflow():
            return self._legacy_convenient()
        return rep

    def _is_on(self) -> bool:
        # Prefer the vendor /power/vs/0 -- the OCF /power/0 is absent on many
        # boards and a stale mirror on some, so reading it first showed
        # pre-write state after a power toggle (issue #53).
        power = self._rep(POWER_VS_HREF).get("x.com.samsung.da.power")
        if power is not None:
            return str(power).lower() == "on"
        return bool(self._rep(POWER_HREF).get("value"))

    def _supported(self, href: str) -> list[str]:
        return list(self._rep(href).get(_SUPPORTED_FIELD) or [])

    def _warn_unmapped(self, href: str, code: str) -> None:
        """Log once per (href, code) when a device-reported mode has no
        entry in the relevant device<->HA map, so a real gap surfaces in
        the log instead of silently vanishing (issue #93).

        Falls back to `unique_id` when `entity_id` is unset (issue #235):
        this can fire during setup's first discovery pass, before the
        entity is added to hass, when entity_id is still None."""
        key = (href, code)
        if key in self._warned_unmapped:
            return
        self._warned_unmapped.add(key)
        _LOGGER.warning(
            "%s: device mode %r on %s has no HA mapping and was dropped; "
            "please file an issue with your diagnostics dump",
            self.entity_id or self.unique_id,
            code,
            href,
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
        authoritative channel, signalled by /temperature/current/0 being
        present. Those boards honor reads/writes on /temperature/desired/0
        and ignore the vendor /temperatures/vs/0; boards without the pair
        are the reverse. Confirmed on live units of both kinds."""
        return bool(self._rep(TEMP_CURRENT_HREF))

    def _temps_vs(self) -> dict:
        """Vendor `/temperatures/vs/0` items[0] (empty {} when absent)."""
        return _temps_vs_item(self._rep(TEMPS_VS_HREF))

    @property
    def temperature_unit(self) -> str:
        raw = self._rep(TEMP_DESIRED_HREF).get("units")
        if raw is None:
            raw = self._temps_vs().get("x.com.samsung.da.unit")
        return (
            UnitOfTemperature.FAHRENHEIT
            if normalize_temp_unit(raw, "°C") == "°F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def current_temperature(self):
        v = _num(self._rep(TEMP_CURRENT_HREF).get("temperature"))
        if v is None:
            v = _num(self._temps_vs().get("x.com.samsung.da.current"))
        return v

    @property
    def target_temperature(self):
        # Read from the same channel writes go to (see async_set_temperature):
        # OCF /temperature/desired/0 on boards with the full OCF pair, vendor
        # /temperatures/vs/0 otherwise -- with the other as fallback.
        ocf = _num(self._rep(TEMP_DESIRED_HREF).get("temperature"))
        vs = _num(self._temps_vs().get("x.com.samsung.da.desired"))
        if self._ocf_temp_authoritative():
            return ocf if ocf is not None else vs
        return vs if vs is not None else ocf

    def _range(self) -> list | None:
        r = self._rep(TEMP_DESIRED_HREF).get("range")
        if isinstance(r, (list, tuple)) and len(r) == 2:
            return r
        item = self._temps_vs()
        lo = _num(item.get("x.com.samsung.da.minimum"))
        hi = _num(item.get("x.com.samsung.da.maximum"))
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
        return (
            _num(self._rep(TEMP_CONTROL_HREF).get("increment"))
            or _num(self._rep(TEMP_CONTROL_HREF).get("x.com.samsung.da.increment"))
            or _num(self._temps_vs().get("x.com.samsung.da.increment"))
            or 1.0
        )

    # -- hvac mode ----------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._is_on():
            return HVACMode.OFF
        device = _first(self._rep(MODE_HREF).get(_MODES_FIELD))
        if device == _AI_COMFORT_MODE:
            return HVACMode.AUTO
        if (
            device is not None
            and device not in _DEVICE_TO_HVAC
            and device not in _NON_HVAC_OPTION_CODES
        ):
            self._warn_unmapped(MODE_HREF, device)
        return _DEVICE_TO_HVAC.get(device, HVACMode.AUTO)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF]
        for m in self._supported(MODE_HREF):
            if m == _AI_COMFORT_MODE or m in _NON_HVAC_OPTION_CODES:
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
        airflow = self._legacy_airflow()
        if airflow:
            return _DEVICE_TO_FAN.get(str(airflow.get("x.com.samsung.da.speedLevel")))
        rep = self._rep(WIND_STRENGTH_HREF)
        code = _first(rep.get(_MODES_FIELD))
        if code is None:
            return None
        return _DEVICE_TO_FAN.get(code) or _wind_strength_label(code, rep)

    @property
    def fan_modes(self) -> list[str]:
        if self._legacy_airflow():
            # This resource carries no supportedModes, so the full scale is offered.
            return list(_DEVICE_TO_FAN.values())
        rep = self._rep(WIND_STRENGTH_HREF)
        modes = []
        for code in self._supported(WIND_STRENGTH_HREF):
            mode = _DEVICE_TO_FAN.get(code) or _wind_strength_label(code, rep)
            if mode not in modes:
                modes.append(mode)
        return modes

    def _swing_via_direction(self) -> bool:
        """True when WIND_DIRECTION_HREF is the swing channel to use --
        signalled by its presence. Boards without it (issue #126) report
        the 2-axis oscillation resource instead; see _oscillation_swing."""
        return bool(self._rep(WIND_DIRECTION_HREF))

    @property
    def swing_mode(self):
        airflow = self._legacy_airflow()
        if airflow:
            return _DEVICE_TO_SWING.get(airflow.get("x.com.samsung.da.direction"))
        if self._swing_via_direction():
            return self._read_mode(WIND_DIRECTION_HREF, _DEVICE_TO_SWING)
        return _oscillation_swing(self._rep(WIND_OSCILLATION_HREF))

    @property
    def swing_modes(self) -> list[str]:
        if self._legacy_airflow():
            return list(_SWING_TO_DEVICE.keys())
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
        # HA's set_temperature service can carry an optional hvac_mode;
        # honor it (setting the mode also powers the unit on) so a dashboard
        # "turn on to Auto 24" button doesn't set the setpoint alone.
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)
            if hvac_mode == HVACMode.OFF:
                return
        temp = kwargs.get("temperature")
        if temp is None:
            return
        # OCF-pair boards write /temperature/desired/0; vendor boards write
        # /temperatures/vs/0 (see airconditioner._climate_write).
        kind = "temperature_ocf" if self._ocf_temp_authoritative() else "temperature"
        await self.coordinator.async_send_command(self._bound, (kind, temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send_command(self._bound, ("power", False))
            return
        device = self._device_code_for_hvac(hvac_mode)
        if device is None:
            return
        if not self._is_on():
            await self.coordinator.async_send_command(self._bound, ("power", True))
        await self.coordinator.async_send_command(self._bound, ("mode", device))

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", True))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send_command(self._bound, ("power", False))

    async def _set_mapped(self, kind: str, mapping: dict, value: str) -> None:
        """Map an HA fan/swing/preset value back to its device code and write it."""
        device = mapping.get(value)
        if device is not None:
            await self.coordinator.async_send_command(self._bound, (kind, device))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self._legacy_airflow():
            level = _FAN_TO_DEVICE.get(fan_mode)
            if level is not None:
                await self.coordinator.async_send_command(self._bound, ("fan_legacy", level))
            return
        supported = self._supported(WIND_STRENGTH_HREF)
        device = _FAN_TO_DEVICE.get(fan_mode)
        # A static hit is only trustworthy if this unit's own supportedModes
        # includes that code -- a board can use non-standard codes (issue
        # #155) while still spelling a standard label in modesName, so the
        # static guess could be a plausible code the device never
        # advertised. Fall through to the live scan when it isn't one of
        # this unit's own codes.
        if device is None or (supported and device not in supported):
            rep = self._rep(WIND_STRENGTH_HREF)
            for code in supported:
                if _wind_strength_label(code, rep) == fan_mode:
                    device = code
                    break
        if device is not None:
            await self.coordinator.async_send_command(self._bound, ("fan", device))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if self._legacy_airflow():
            code = _SWING_TO_DEVICE.get(swing_mode)
            if code is not None:
                await self.coordinator.async_send_command(self._bound, ("swing_legacy", code))
            return
        if self._swing_via_direction():
            await self._set_mapped("swing", _SWING_TO_DEVICE, swing_mode)
            return
        if self._rep(WIND_OSCILLATION_HREF):
            await self.coordinator.async_send_command(self._bound, ("oscillation", swing_mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_AI_COMFORT:
            # Writes the primary mode resource, not the convenient one --
            # 'AIComfort' lives in /mode/vs/0 alongside Cool/Dry/Auto, not in
            # /mode/convenient/vs/0 with Quiet/Smart/Speed/Sleep.
            await self.coordinator.async_send_command(self._bound, ("mode", _AI_COMFORT_MODE))
            return
        # Reverse-resolve against the unit's own supportedModes (codes aren't
        # a fixed transform of the HA value -- e.g. 'NanoSleep' -> 'nanosleep').
        for code in self._supported(CONVENIENT_HREF):
            if _preset_to_ha(code) == preset_mode:
                kind = "preset_legacy" if self._legacy_preset() else "preset"
                await self.coordinator.async_send_command(self._bound, (kind, code))
                return

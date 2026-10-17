"""Tests for the AC HVAC-mode/preset device<->HA maps in climate.py (issue #93).

Module-level dicts/constants with no coordinator/entity dependency, so --
like `_temps_vs_item` in test_climate_temperature_fallback.py -- they're
testable directly.
"""

from homeassistant.components.climate import HVACMode

from custom_components.localthings.climate import (
    _AI_COMFORT_MODE,
    _DEVICE_TO_HVAC,
    _HVAC_TO_DEVICE,
    _preset_to_ha,
)


def test_auto_maps_to_hvac_auto():
    """The device's 'Auto' is a single-setpoint "device decides" mode -> HA
    HVACMode.AUTO, not HEAT_COOL (issue #91 review): HEAT_COOL implies a
    two-setpoint heat+cool range these single-setpoint units (including
    cool-only models) don't have. AIComfort is handled separately, not
    folded into this map."""
    assert _DEVICE_TO_HVAC["Auto"] == HVACMode.AUTO


def test_aicomfort_not_in_flat_hvac_map():
    """AIComfort isn't a flat _DEVICE_TO_HVAC entry -- it's an AI overlay on
    top of 'Auto', modeled as hvac_mode=AUTO instead of a distinct HVACMode
    value (see the climate.py module comment)."""
    assert _AI_COMFORT_MODE not in _DEVICE_TO_HVAC


def test_hvac_auto_fallback_is_plain_auto():
    """_HVAC_TO_DEVICE is only the fallback for a unit reporting no
    supportedModes. AIComfort isn't a flat _DEVICE_TO_HVAC entry (see
    test_aicomfort_not_in_flat_hvac_map), so it can never win the reverse
    {v: k} dict; _device_code_for_hvac() picks it from supportedModes."""
    assert _HVAC_TO_DEVICE[HVACMode.AUTO] == "Auto"


def test_fan_only_still_reachable_via_wind():
    """Guard against regressing the existing 'Wind' -> FAN_ONLY entry while
    editing this map."""
    assert _DEVICE_TO_HVAC["Wind"] == HVACMode.FAN_ONLY


def test_fan_only_still_reachable_via_fan():
    """'Fan' (e.g. TP1X_DA-AC-RAC-01011) is a second FAN_ONLY spelling
    alongside 'Wind' -- issue #91."""
    assert _DEVICE_TO_HVAC["Fan"] == HVACMode.FAN_ONLY


def test_fan_only_reverse_fallback_prefers_wind():
    """_device_code_for_hvac() resolves FAN_ONLY from a unit's own
    supportedModes first, so _HVAC_TO_DEVICE is only a fallback for a unit
    reporting no supportedModes at all. That fallback must stay 'Wind' (the
    original single spelling, predating 'Fan') rather than silently
    flipping to whichever of the two duplicate-value entries happens to
    come last in _DEVICE_TO_HVAC."""
    assert _HVAC_TO_DEVICE[HVACMode.FAN_ONLY] == "Wind"


def test_preset_to_ha_off_maps_to_preset_none():
    from homeassistant.components.climate import PRESET_NONE

    assert _preset_to_ha("Off") == PRESET_NONE


def test_preset_to_ha_lowercases_other_codes():
    """Every other device code is exposed as its lowercased self -- resolved
    dynamically, not via a per-model table (issue #91)."""
    assert _preset_to_ha("Sleep") == "sleep"
    assert _preset_to_ha("NanoSleep") == "nanosleep"
    assert _preset_to_ha("MotionIndirect") == "motionindirect"


def test_fac_bora_wind_strength_codes_fit_the_standard_scale():
    """TP2X_FAC_BORA_21K's (issues #150/#153) /wind/strength/vs/0 codes
    (0/2/3/4, skipping 1/'low') and modesName (Auto/Mid/High/Turbo) already
    match _DEVICE_TO_FAN's own mapping exactly -- no dynamic modesName
    fallback needed for this particular board, unlike issue #155's
    TP1X_DA-AC-RAC-01001_0000.

    Asserts the live climate entity's actual fan_modes/fan_mode output
    (not just that the module constant _DEVICE_TO_FAN happens to have
    these keys) -- a bare `code in _DEVICE_TO_FAN` check would still pass
    even if fan_modes/fan_mode were completely broken, since it never
    touches the entity at all.
    """
    from typing import ClassVar, cast

    from custom_components.localthings.climate import LocalThingsClimate
    from custom_components.localthings.coordinator import LocalThingsCoordinator
    from custom_components.localthings.registry import by_type
    from custom_components.localthings.registry.discovery import discover
    from custom_components.localthings.registry.entities import ClimateDesc
    from tests.conftest import _load_device

    class _FakeCoordinator:
        device_key = "TEST-FAC-BORA-SERIAL"
        device_info: ClassVar[dict] = {}
        data: ClassVar[dict] = {}

        def __init__(self, resources):
            self.last_resources = resources

        def resource(self, href):
            return self.last_resources.get(href, {})

        def canonical_resources(self, subdevice):
            # This test's entity uses the default MAIN subdevice, so the
            # canonical view is just the raw snapshot (issue #177).
            return self.last_resources

        def learned_modes(self, href):
            return []

    resources = _load_device("airconditioner_fac_bora")
    info = resources["/information/vs/0"]
    reg = by_type.for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate_bound = next(item for item in bound if isinstance(item.desc, ClimateDesc))
    entity = LocalThingsClimate(
        cast(LocalThingsCoordinator, _FakeCoordinator(resources)), climate_bound
    )

    assert entity.fan_modes == ["auto", "medium", "high", "turbo"]
    assert entity.fan_mode == "auto"

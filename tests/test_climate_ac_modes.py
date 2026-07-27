"""Tests for the AC HVAC-mode/preset device<->HA maps in climate.py (issue #93).

Module-level dicts/constants with no coordinator/entity dependency, so --
like `_temps_vs_item` in test_climate_temperature_fallback.py -- they're
testable directly.
"""
from homeassistant.components.climate import HVACMode

from custom_components.localthings.climate import (
    _AI_COMFORT_MODE, _DEVICE_TO_HVAC, _HVAC_TO_DEVICE, PRESET_AI_COMFORT,
)


def test_auto_still_maps_to_heat_cool():
    """'Auto' is unchanged -- AIComfort is handled separately, not folded
    into this map."""
    assert _DEVICE_TO_HVAC['Auto'] == HVACMode.HEAT_COOL


def test_aicomfort_not_in_flat_hvac_map():
    """AIComfort isn't a flat _DEVICE_TO_HVAC entry -- it's an AI overlay on
    top of 'Auto', modeled as hvac_mode=AUTO + a dedicated preset instead of
    a distinct HVACMode value (see the climate.py module comment)."""
    assert _AI_COMFORT_MODE not in _DEVICE_TO_HVAC


def test_hvac_auto_not_writable_via_hvac_mode():
    """HVACMode.AUTO has no _DEVICE_TO_HVAC entry, so it's unreachable via
    async_set_hvac_mode -- entered/left only through the ai_comfort preset."""
    assert HVACMode.AUTO not in _HVAC_TO_DEVICE


def test_fan_only_still_reachable_via_wind():
    """Guard against regressing the existing 'Wind' -> FAN_ONLY entry while
    editing this map."""
    assert _DEVICE_TO_HVAC['Wind'] == HVACMode.FAN_ONLY


def test_preset_ai_comfort_constant():
    assert PRESET_AI_COMFORT == 'ai_comfort'

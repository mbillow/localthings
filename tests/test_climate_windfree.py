"""Tests for the horizontal-swing mapping added to climate.py for issue #75
-- pure dict lookups, testable without a coordinator/entity fixture (see
test_climate_temperature_fallback.py).

The preset side of issue #75 (WindFree/motion convenient modes not
surfacing) is intentionally not addressed here: PR #91 replaces
climate.py's static _DEVICE_TO_PRESET table with a generic resolver that
reads any device preset code straight off the unit's own supportedModes,
which covers WindFree/motion generically instead of a per-model dict --
duplicating that here would just conflict with it.
"""
from custom_components.localthings.climate import _DEVICE_TO_SWING, _SWING_TO_DEVICE


def test_horizontal_swing_maps_both_directions():
    assert _DEVICE_TO_SWING['Left_And_Right'] == 'horizontal'
    assert _SWING_TO_DEVICE['horizontal'] == 'Left_And_Right'


def test_existing_swing_modes_unchanged():
    assert _DEVICE_TO_SWING['Fix'] == 'off'
    assert _DEVICE_TO_SWING['All'] == 'both'
    assert _DEVICE_TO_SWING['Up_And_Low'] == 'vertical'

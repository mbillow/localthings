"""Tests for the /temperatures/vs/0 fallback helper in climate.py (PR #36).

`_temps_vs_item` is a pure module-level function (plain dict in, plain dict
out) with no coordinator/entity dependency, so -- like select.py's `_display`
in test_select_display.py -- it's testable directly even though the
LocalThingsClimate entity itself needs a coordinator/hass fixture to
exercise. This covers the actual fallback-selection logic that the
registry-level "no unbound hrefs" tests in test_airconditioner_capabilities.py
don't reach: TP1X-class firmware has no OCF-standard
/temperature/current+desired pair, so current_temperature/target_temperature
would silently be None without this fallback.
"""
from custom_components.localthings.climate import _temps_vs_item


def test_temps_vs_item_returns_first_item():
    rep = {
        'x.com.samsung.da.items': [
            {'x.com.samsung.da.current': '17.0', 'x.com.samsung.da.desired': '25.0'},
        ],
    }
    assert _temps_vs_item(rep) == {
        'x.com.samsung.da.current': '17.0', 'x.com.samsung.da.desired': '25.0',
    }


def test_temps_vs_item_empty_when_items_missing():
    """OCF-pair devices don't populate /temperatures/vs/0's items at all --
    callers must fall through to the OCF hrefs cleanly, not raise."""
    assert _temps_vs_item({}) == {}


def test_temps_vs_item_empty_when_items_is_empty_list():
    assert _temps_vs_item({'x.com.samsung.da.items': []}) == {}


def test_temps_vs_item_empty_when_items_entry_is_not_a_dict():
    assert _temps_vs_item({'x.com.samsung.da.items': ['not-a-dict']}) == {}

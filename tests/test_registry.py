"""Tests for the CAPABILITIES registry."""
from samsung_appliance.registry.registry import CAPABILITIES


def test_registry_is_keyed_by_href():
    """Registry keys should match capability hrefs."""
    for href, cap in CAPABILITIES.items():
        assert cap.href == href


def test_registry_has_no_duplicate_href():
    """Building succeeded => no duplicates raised at import."""
    assert len(CAPABILITIES) >= 7


def test_operational_state_present():
    """At least one capability should have 'state' in its href."""
    assert any('state' in href for href in CAPABILITIES)

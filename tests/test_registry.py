"""Tests for the CAPABILITIES registry."""
from custom_components.localthings.ocf.registry.registry import CAPABILITIES


def test_registry_is_keyed_by_href():
    """Registry keys should match capability hrefs for all caps in the group."""
    for href, caps in CAPABILITIES.items():
        for cap in caps:
            assert cap.href == href


def test_registry_has_no_duplicate_href():
    """Building succeeded => no duplicates raised at import."""
    assert len(CAPABILITIES) >= 7


def test_operational_state_present():
    """At least one capability should have 'state' in its href."""
    assert any('state' in href for href in CAPABILITIES)

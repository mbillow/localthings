"""Tests that known-noise hrefs don't show up as coverage gaps.

Each device-type registry folds in registry.capabilities.ignored.IGNORED so
that Bixby/voice/network/OTA housekeeping resources aren't reported as
unbound. This guards two things at once: that the ignored hrefs are
actually suppressed, and (via a plain import) that no ignored href
collides with a real capability declared elsewhere for the same family.
"""
import json
from pathlib import Path

from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import dishwasher, refrigerator
from custom_components.localthings.registry.capabilities.ignored import IGNORED

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

_IGNORED_HREFS = {cap.href for cap in IGNORED}


def _load(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text())
    return parse_device0_batch(data['device0'])


def _unbound_hrefs(resources, registry) -> list[str]:
    unbound = []
    from custom_components.localthings.registry.discovery import discover
    discover(resources, registry.capabilities, registry.pattern_capabilities,
              log=unbound.append)
    return unbound


def test_ignored_hrefs_absent_from_dishwasher_gaps():
    resources = _load('dishwasher_device.json')
    unbound = _unbound_hrefs(resources, dishwasher.REGISTRY)
    present_ignored = _IGNORED_HREFS & set(unbound)
    assert not present_ignored, f"ignored hrefs leaked into gap report: {present_ignored}"


def test_ignored_hrefs_absent_from_refrigerator_gaps():
    resources = _load('refrigerator_device.json')
    unbound = _unbound_hrefs(resources, refrigerator.REGISTRY)
    present_ignored = _IGNORED_HREFS & set(unbound)
    assert not present_ignored, f"ignored hrefs leaked into gap report: {present_ignored}"


def test_dishwasher_fixture_still_has_genuine_gaps():
    """Sanity check that the gap mechanism isn't silencing everything.

    If this starts failing because the fixture is now fully covered, that's
    good news — just trim the expected set down (or to empty) rather than
    deleting the test.
    """
    resources = _load('dishwasher_device.json')
    unbound = set(_unbound_hrefs(resources, dishwasher.REGISTRY))
    assert '/wm/jobbeginingstatus/vs/0' in unbound
    assert '/diagnosis/vs/0' in unbound

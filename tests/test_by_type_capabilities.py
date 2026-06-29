"""Tests for device-type registries and DOOR_GENERIC pattern capability."""
import json
from pathlib import Path

import pytest

from samsung_appliance.registry.adapter import _key
from samsung_appliance.registry.by_type import dishwasher, refrigerator
from samsung_appliance.registry.capabilities import fridge
from samsung_appliance.registry.discovery import discover

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'
DUMPS = Path(__file__).resolve().parent.parent / 'local-tools' / 'dumps'


def _load_resources(ip: str) -> dict[str, dict]:
    data = json.loads((DUMPS / f'{ip}.json').read_text())
    return {k: v for k, v in data['resources'].items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# Integration: dishwasher and refrigerator registries
# ---------------------------------------------------------------------------

def test_dishwasher_registry_discovers_expected_keys():
    """Discover over dishwasher dump and verify golden state_keys are a subset."""
    resources = _load_resources('10.0.0.129')
    reg = dishwasher.REGISTRY
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    actual_keys = {_key(b) for b in bound}
    golden = json.loads((GOLDEN / 'dishwasher.json').read_text())
    golden_keys = set(golden['state_keys'])
    missing = golden_keys - actual_keys
    assert not missing, (
        f"Golden keys not found in dishwasher discover output:\n  {sorted(missing)}"
    )


def test_refrigerator_registry_discovers_expected_keys():
    """Discover over refrigerator dump and verify golden state_keys are a subset."""
    resources = _load_resources('10.0.0.254')
    reg = refrigerator.REGISTRY
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    actual_keys = {_key(b) for b in bound}
    golden = json.loads((GOLDEN / 'refrigerator.json').read_text())
    golden_keys = set(golden['state_keys'])
    missing = golden_keys - actual_keys
    assert not missing, (
        f"Golden keys not found in refrigerator discover output:\n  {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# DOOR_GENERIC pattern capability unit tests
# ---------------------------------------------------------------------------

def test_door_generic_binds_unknown_door_href():
    """/door/wine/0 binds via DOOR_GENERIC (href_prefix='/door/') with key door_wine_open."""
    resources = {
        '/door/wine/0': {'openState': 'Open'},
    }
    bound = discover(resources, {}, [fridge.DOOR_GENERIC])
    assert len(bound) == 1
    b = bound[0]
    assert _key(b) == 'door_wine_open', f"Expected 'door_wine_open', got {_key(b)!r}"


def test_door_generic_does_not_bind_doors_aggregate():
    """/doors/vs/0 does not start with '/door/' so DOOR_GENERIC must not bind it."""
    resources = {
        '/doors/vs/0': {
            'x.com.samsung.da.items': [],
        },
    }
    bound = discover(resources, {}, [fridge.DOOR_GENERIC])
    assert bound == [], f"Expected no bindings, got {bound}"


def test_door_generic_does_not_bind_known_door():
    """/door/cooler/0 is claimed by DOOR_FRIDGE exact cap; DOOR_GENERIC must not also bind it."""
    resources = {
        '/door/cooler/0': {'openState': 'Closed'},
    }
    reg = refrigerator.REGISTRY
    bound = discover(resources, reg.capabilities, [fridge.DOOR_GENERIC])
    # Should be bound only by DOOR_FRIDGE (exact cap), not DOOR_GENERIC
    cooler_bindings = [b for b in bound if b.href == '/door/cooler/0']
    assert len(cooler_bindings) > 0, "Expected at least one binding for /door/cooler/0"
    for b in cooler_bindings:
        assert b.capability is fridge.DOOR_FRIDGE, (
            f"Expected capability DOOR_FRIDGE, got {b.capability!r}"
        )
        # key_override should be None when bound via exact cap
        assert b.key_override is None, (
            f"Expected no key_override (exact cap binding), got {b.key_override!r}"
        )

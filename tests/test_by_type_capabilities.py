"""Tests for device-type registries and DOOR_GENERIC pattern capability."""
import json
from pathlib import Path

import pytest

from custom_components.localthings.registry.adapter import _key
from custom_components.localthings.registry.by_type import dishwasher, refrigerator
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_resources

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'


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
    # entity key 'open' + href segs ['door','wine'] -> 'door_wine_open'
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


def test_door_generic_binds_cooler_door_with_auto_derived_key():
    """/door/cooler/0 is now handled by DOOR_GENERIC pattern cap with auto-derived key door_cooler_open."""
    resources = {
        '/door/cooler/0': {'openState': 'Closed'},
    }
    reg = refrigerator.REGISTRY
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    cooler_bindings = [b for b in bound if b.href == '/door/cooler/0']
    assert len(cooler_bindings) == 1, (
        f"Expected exactly one binding for /door/cooler/0, got {len(cooler_bindings)}"
    )
    b = cooler_bindings[0]
    assert b.capability is fridge.DOOR_GENERIC, (
        f"Expected DOOR_GENERIC, got {b.capability!r}"
    )
    assert _key(b) == 'door_cooler_open', f"Expected 'door_cooler_open', got {_key(b)!r}"

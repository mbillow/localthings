"""Verify that multi-instance fridge resources produce distinct entity keys.

The two physical door resources (/door/cooler/0, /door/freezer/0) and the two
ice-maker resources (/icemaker/one/vs/0, /icemaker/two/vs/0) use named path
segments rather than numeric indices, so they are modelled via DOOR_GENERIC
pattern capability that auto-derives distinct keys from the href segments.
"""
from tests.conftest import _load_resources

from samsung_appliance.registry.adapter import _key
from samsung_appliance.registry.by_type import refrigerator
from samsung_appliance.registry.discovery import discover


def test_two_doors_get_distinct_keys():
    resources = _load_resources('10.0.0.254')
    reg = refrigerator.REGISTRY
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    door_keys = sorted(_key(b) for b in bound if _key(b).startswith('door'))
    # at least the two physical doors (cooler + freezer) produce distinct keys
    assert len(set(door_keys)) >= 2, f"Expected >= 2 distinct door keys, got: {door_keys}"


def test_two_iceMakers_get_distinct_keys():
    resources = _load_resources('10.0.0.254')
    reg = refrigerator.REGISTRY
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    ice_keys = sorted(_key(b) for b in bound if _key(b).startswith('icemaker_'))
    # icemaker_one_* and icemaker_two_* are distinct
    ice_one_keys = [k for k in ice_keys if k.startswith('icemaker_one_')]
    ice_two_keys = [k for k in ice_keys if k.startswith('icemaker_two_')]
    assert ice_one_keys, "No icemaker_one_* keys found"
    assert ice_two_keys, "No icemaker_two_* keys found"
    assert set(ice_one_keys).isdisjoint(set(ice_two_keys)), \
        f"icemaker_one and icemaker_two keys overlap: {ice_one_keys} vs {ice_two_keys}"

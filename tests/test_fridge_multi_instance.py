"""Verify that multi-instance fridge resources produce distinct entity keys.

The two physical door resources (/door/cooler/0, /door/freezer/0) and the two
ice-maker resources (/icemaker/one/vs/0, /icemaker/two/vs/0) use named path
segments rather than numeric indices, so they are modelled as separate
Capability objects with distinct entity keys.
"""
import json

from samsung_appliance.registry.registry import CAPABILITIES
from samsung_appliance.registry.discovery import discover


def test_two_doors_get_distinct_keys():
    res = json.load(open('local-tools/dumps/10.0.0.254.json'))['resources']
    bound = discover(res, CAPABILITIES)
    door_keys = sorted(f"{b.desc.key}{b.instance}" for b in bound
                       if b.desc.key.startswith('door'))
    # at least the two physical doors produce distinct keys
    assert len(set(door_keys)) >= 2, f"Expected >= 2 distinct door keys, got: {door_keys}"


def test_two_iceMakers_get_distinct_keys():
    res = json.load(open('local-tools/dumps/10.0.0.254.json'))['resources']
    bound = discover(res, CAPABILITIES)
    ice_keys = sorted(f"{b.desc.key}{b.instance}" for b in bound
                      if b.desc.key.startswith('ice'))
    # ice1_* and ice2_* are distinct
    ice1_keys = [k for k in ice_keys if k.startswith('ice1')]
    ice2_keys = [k for k in ice_keys if k.startswith('ice2')]
    assert ice1_keys, "No ice1_* keys found"
    assert ice2_keys, "No ice2_* keys found"
    assert set(ice1_keys).isdisjoint(set(ice2_keys)), \
        f"ice1 and ice2 keys overlap: {ice1_keys} vs {ice2_keys}"

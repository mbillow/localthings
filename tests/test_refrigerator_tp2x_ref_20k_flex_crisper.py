"""TP2X_REF_20K with a flex crisper drawer (issue #464).

/status/pantry/one/vs/0 names itself FLEX_CRISPER and offers the flex
zone's RF9000A options without their CV_ prefix, which the pantry zone's
translation table did not cover. No other fixture has this drawer.
"""

from custom_components.localthings.catalog import translated_states
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.select import _display
from tests.conftest import _load_device

FIXTURE = "refrigerator_tp2x_ref_20k_flex_crisper"


def _bind():
    resources = _load_device(FIXTURE)
    reg = resolve(resources)
    assert reg is not None
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return reg, bound, resources, unbound


def test_resolves_as_a_refrigerator_with_no_unbound_hrefs():
    reg, _bound, _resources, unbound = _bind()
    assert reg.name == "refrigerator"
    assert unbound == []


def test_every_pantry_and_flex_zone_option_is_named():
    _reg, bound, resources, _unbound = _bind()
    state = flatten(bound, resources)
    assert state["pantry_zone_mode"] == "TTYPE_RF9000A_FRIDGE"
    for key in ("pantry_zone_mode", "flex_zone_mode"):
        known = translated_states("select", key)
        rep = resources[next(b.href for b in bound if b.desc.key == key)]
        options = rep["x.com.samsung.da.supportedOptions"]
        assert options
        assert all(_display(option, key) in known for option in options), key

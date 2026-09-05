"""Coverage for the DA_WM_TP1_21_COMMON washer/dryer pair in issues #437/#438.

One reporter, two separate appliances on the same board and firmware -- a
WF80H washer and a DV80H27H dryer, both KR market. They arrived as two
config entries, not as one composite: each reports numofsubdevice 1 with
/device/1 and /device/2 both 4.04.

Both dumps resolve on /oic/d (oic.d.washer / oic.d.dryer) and on the
description-based consumer prefix, so these tests pin both paths -- the
goldens run without device_types, which is the weaker of the two.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc, SwitchDesc
from tests.conftest import _load_device

WASHER = "washer_wf80h"
DRYER = "dryer_dv80h"


def _bind(name, device_types=()):
    resources = _load_device(name)
    reg = resolve(resources, device_types=device_types)
    assert reg is not None, f"{name} fell back to the unknown-device registry"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return reg, resources, bound, unbound


def _desc(bound, key, kind):
    return next(b.desc for b in bound if b.desc.key == key and isinstance(b.desc, kind))


def test_washer_resolves_and_binds_every_href():
    reg, _resources, _bound, unbound = _bind(WASHER)
    assert reg.name == "washer"
    assert unbound == []


def test_dryer_resolves_and_binds_every_href():
    reg, _resources, _bound, unbound = _bind(DRYER)
    assert reg.name == "dryer"
    assert unbound == []


def test_oic_device_type_routes_both_the_same_way():
    """/oic/d is stage one, so it must not disagree with the consumer-prefix
    fallback the goldens exercise."""
    assert _bind(WASHER, ("oic.wk.d", "oic.d.washer"))[0].name == "washer"
    assert _bind(DRYER, ("oic.wk.d", "oic.d.dryer"))[0].name == "dryer"


def test_framemems_and_displaymode_are_ignored_not_bound():
    """The two hrefs that fired the coverage-gap repair resolve to no-entity
    capabilities, so they clear the repair without inventing entities."""
    for name in (WASHER, DRYER):
        _reg, resources, bound, _unbound = _bind(name)
        keys = {b.href for b in bound}
        assert "/wm/displaymode/state/vs/0" in resources
        assert "/wm/displaymode/state/vs/0" not in keys
    _reg, resources, bound, _unbound = _bind(WASHER)
    assert "/rm/framemems/vs/0" in resources
    assert "/rm/framemems/vs/0" not in {b.href for b in bound}


def test_washer_auto_dispense_switches_read_both_states():
    """The reporter's washer had auto detergent on and auto softener off in
    the same dump -- the pair of states that makes these bindable."""
    _reg, resources, bound, _unbound = _bind(WASHER)
    state = flatten(bound, resources)
    assert state["auto_detergent"] is True
    assert state["auto_softener"] is False


def test_washer_auto_dispense_writes_target_the_wash_resource():
    _reg, _resources, bound, _unbound = _bind(WASHER)
    desc = _desc(bound, "auto_detergent", SwitchDesc)
    assert desc.write_fn("Off", {}) == (
        ["washer", "vs", "0"],
        {"x.com.samsung.da.autoDetergentEnabled": "Off"},
    )
    assert desc.write_fn("bogus", {}) is None


def test_dryer_dry_level_options_come_from_the_device():
    """supportedDryLevel/supportedDryTime drive the options, so a board with
    a different set (dv6800n reports '1'/'2'/'3') is not measured against a
    hardcoded tuple."""
    _reg, resources, bound, _unbound = _bind(DRYER)
    level = _desc(bound, "dry_level", SelectDesc)
    assert level.options_field == "x.com.samsung.da.supportedDryLevel"
    assert resources["/washer/vs/0"][level.options_field] == [
        "None",
        "Damp",
        "Less",
        "Normal",
        "More",
    ]
    assert level.write_fn("More", {}) == (
        ["washer", "vs", "0"],
        {"x.com.samsung.da.dryLevel": "More"},
    )


def test_dryer_dry_time_self_gates_on_supported_list():
    """dve50a8600 reports supportedDryLevel but no supportedDryTime; the
    select must disappear there rather than offering an empty list."""
    _reg, _resources, bound, _unbound = _bind(DRYER)
    desc = _desc(bound, "dry_time", SelectDesc)
    assert desc.exists_fn({"x.com.samsung.da.supportedDryTime": ["00:00:00"]}, {})
    assert not desc.exists_fn({"x.com.samsung.da.dryTime": "00:00:00"}, {})


def test_washer_delay_start_is_reported_separately_from_the_wash():
    """This washer was captured mid-delay: remainingTime covers the whole
    wait, and the delay end is what drives delay_start_hours."""
    _reg, resources, bound, _unbound = _bind(WASHER)
    state = flatten(bound, resources)
    assert state["machine_state"] == "active"
    assert state["progress"] == "delaywash"
    assert state["delay_start_hours"] > 0

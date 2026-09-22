"""Coverage for the ARTIK051_GB_WO_003 Dual Cook Flex wall oven (issue #490).

Two cavities over one connection (Pattern A, indexed siblings), and the
first board in the corpus to advertise the OCF-standard
`/temperature/{current,desired}/{cook,prob}/N` pair alongside a fully
populated vendor `/temperatures/vs/0`. Those six hrefs were the reporter's
unbound-coverage gap.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SensorDesc
from custom_components.localthings.registry.subdevices import MAIN, canonical_view
from tests.conftest import _discover_full, _load_device_full

FIXTURE = "oven_nv75n_dual_cook"
DEVICE_TYPES = ("oic.wk.d", "oic.d.oven")


def _pipeline():
    resources, oic_res, seeds = _load_device_full(FIXTURE)
    return _discover_full(resources, oic_res, seeds, DEVICE_TYPES), resources


def _unbound():
    (_bound, materialized, _skipped, full, _name), resources = _pipeline()
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    gaps: list[str] = []
    for sub in (MAIN, *materialized):
        discover(
            canonical_view(sub, full, materialized),
            reg.capabilities,
            reg.pattern_capabilities,
            log=gaps.append,
            subdevice=sub,
        )
    return gaps


def _master_state():
    (bound, materialized, _skipped, full, _name), _resources = _pipeline()
    return flatten(
        [b for b in bound if b.subdevice is MAIN], canonical_view(MAIN, full, materialized)
    )


def test_resolves_to_oven_and_finds_the_second_cavity():
    (_bound, materialized, skipped, _full, name), _resources = _pipeline()
    assert name == "oven"
    assert [(s.kind, s.key) for s in materialized] == [("indexed", "1")]
    assert skipped == []


def test_the_ocf_temperature_pair_leaves_no_coverage_gap():
    """The six hrefs from the issue, across both cavities. They bind but
    stand down (the vendor array wins here), which is still enough to clear
    the gap -- discover() only reports an href no capability claims."""
    assert _unbound() == []


def test_the_vendor_array_wins_while_it_is_present():
    """One temperature entity per cavity, not two. The OCF pair carries the
    same values, so binding both would produce duplicate entities that
    disagree only in rounding."""
    state = _master_state()
    assert "current_temp_c" in state
    assert "oven_setpoint" in state
    bound_hrefs = {
        b.href for b in _pipeline()[0][0] if b.desc.key in ("current_temp_c", "oven_setpoint")
    }
    assert bound_hrefs == {"/temperatures/vs/0", "/temperatures/vs/1"}


def test_the_ocf_pair_takes_over_when_the_vendor_array_is_absent():
    """The fallback's whole point: strip the vendor href and the same
    entity keys must come back off the OCF resources, so a board with only
    those is not a board with no oven temperature."""
    resources, _oic_res, _seeds = _load_device_full(FIXTURE)
    ocf_only = {h: rep for h, rep in resources.items() if h != "/temperatures/vs/0"}
    reg = resolve(ocf_only, device_types=DEVICE_TYPES)
    assert reg is not None
    gaps: list[str] = []
    bound = discover(ocf_only, reg.capabilities, reg.pattern_capabilities, log=gaps.append)
    state = flatten(bound, ocf_only)

    assert gaps == []
    assert state["current_temp_c"] == 0.0
    assert state["oven_setpoint"] == 0.0
    # The probe pair is not gated on the mode token -- that gate belongs to
    # the vendor items[] reading, which isn't what answered here.
    assert state["food_probe_temp"] == 0.0


def test_the_food_probe_stays_hidden_while_it_is_unplugged():
    """This dump reports `meatprobe_disconnected`, so items[1] reads a flat
    0 that would otherwise ship as a permanent 0 degree probe."""
    state = _master_state()
    assert "food_probe_temp" not in state
    assert "food_probe_setpoint" not in state


def test_the_food_probe_appears_once_the_board_says_it_is_plugged_in():
    """The gate, exercised against the fixture's own reps with only the
    socket token changed -- the one field a dump taken with a probe fitted
    would differ in."""
    resources, _oic_res, _seeds = _load_device_full(FIXTURE)
    mode = dict(resources["/mode/vs/0"])
    mode["x.com.samsung.da.options"] = [
        "meatprobe_connected" if o.startswith("meatprobe_") else o
        for o in mode["x.com.samsung.da.options"]
    ]
    connected = {**resources, "/mode/vs/0": mode}

    reg = resolve(connected, device_types=DEVICE_TYPES)
    assert reg is not None
    bound = discover(connected, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, connected)

    # items[1] on this dump: current/desired both '0', increment 1.
    assert state["food_probe_temp"] == 0
    assert state["food_probe_setpoint"] == 0


def test_the_probe_reads_items_by_id_not_by_position():
    """A board listing the probe first must not report it as the cavity."""
    desc = next(
        e
        for e in oven.OVEN_SETPOINT.entities
        if isinstance(e, SensorDesc) and e.key == "food_probe_temp"
    )
    swapped = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.id": "1", "x.com.samsung.da.current": "70"},
            {"x.com.samsung.da.id": "0", "x.com.samsung.da.current": "180"},
        ]
    }
    assert desc.rep_fn is not None
    assert desc.rep_fn(swapped) == 70


def test_the_lower_cavity_has_no_probe_of_its_own():
    """Only the upper cavity carries a probe socket: one items[] entry, no
    `/temperature/*/prob/*` pair, no `meatprobe_` token."""
    (bound, materialized, _skipped, full, _name), _resources = _pipeline()
    sub = materialized[0]
    view = canonical_view(sub, full, materialized)
    state = flatten([b for b in bound if b.subdevice is sub], view)

    assert "food_probe_temp" not in state
    assert "/temperature/current/prob/0" not in view
    assert len(view["/temperatures/vs/0"]["x.com.samsung.da.items"]) == 1

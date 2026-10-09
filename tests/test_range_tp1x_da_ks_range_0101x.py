"""Dual-cavity range (TP1X_DA-KS-RANGE-0101X, NE63T8751SG/AA-class, issue
#324): no /information/vs/0 at all, so the modelNum-based fallback has
nothing to read -- routing depends entirely on /oic/d's own device type
(`oic.d.range`), read separately from the /device/0 batch (see
registry/identity.py and by_type/__init__.py's `_OIC_TYPE_TO_KEY`).

The second oven cavity answers as an indexed sibling at /device/1 (Pattern
A, same mechanism as the AC family's issue #177 fixtures) -- the master's
/mode/vs/0 (defaultMode 'UpperConvectionBake') is the upper cavity, the
subdevice's canonical /mode/vs/0 (defaultMode 'LowerConvectionBake') is the
lower one.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from tests.conftest import _discover_full, _load_device_full

FIXTURE = "range_tp1x_da_ks_range_0101x"
DEVICE_TYPES = ("oic.wk.d", "oic.d.range")


def _discover():
    resources, oic_res, seeds = _load_device_full(FIXTURE)
    return _discover_full(resources, oic_res, seeds, DEVICE_TYPES)


def test_resolves_via_oic_type():
    reg = for_device_by_oic_type(DEVICE_TYPES)
    assert reg is not None and reg.name == "range"


def test_resolves_via_the_full_resolve_entrypoint():
    resources, _oic_res, _seeds = _load_device_full(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "range"


def test_no_unbound_hrefs():
    """Every resource in the issue #324 dump binds or is ignored, on both
    the master (upper cavity) and the materialized second cavity -- clears
    the coverage-gap repair."""
    resources, oic_res, seeds = _load_device_full(FIXTURE)
    unbound = []
    from custom_components.localthings.registry.registry import CAPABILITIES
    from custom_components.localthings.registry.subdevices import (
        discover_partitioned,
        enumerate_subdevices,
    )
    from tests.conftest import FakeCoapSession

    sess = FakeCoapSession(seeds)
    candidates, extra = enumerate_subdevices(sess, resources, oic_res)
    full_resources = {**resources, **extra}
    discover_partitioned(
        full_resources,
        candidates,
        resolve,
        CAPABILITIES,
        log=unbound.append,
        oic_device_types=DEVICE_TYPES,
    )
    assert unbound == []


def test_second_cavity_materializes_as_an_indexed_subdevice():
    _bound, materialized, skipped, _full_resources, device_type_name = _discover()
    assert device_type_name == "range"
    assert skipped == []
    assert [(s.kind, s.key) for s in materialized] == [("indexed", "1")]
    assert materialized[0].seed_path == ("device", "1")


def test_both_cavities_expose_distinct_oven_state():
    bound, _materialized, _skipped, full_resources, _name = _discover()
    state = flatten(bound, full_resources)
    for key in ("oven_mode", "oven_setpoint", "oven_state", "machine_state", "door_open"):
        assert key in state, key
    # Second cavity's entities carry the subdevice1_ prefix (adapter._key) --
    # the master's own keys stay unprefixed, so both cavities get distinct
    # unique_ids rather than colliding on the same entity key.
    assert "subdevice1_oven_mode" in state
    assert "subdevice1_oven_setpoint" in state


def test_cooktop_monitoring_present_on_the_master_only():
    """The cooktop half belongs to the appliance as a whole, not either oven
    cavity -- /cooktopmonitoring/vs/0 has no per-cavity index."""
    bound, _materialized, _skipped, full_resources, _name = _discover()
    state = flatten(bound, full_resources)
    assert "cooktop_running_state" in state
    assert "warming_center_state" in state
    assert "subdevice1_cooktop_running_state" not in state


def test_every_cavity_mode_is_named():
    """With the divider in, the upper cavity offers only Upper* modes and the
    lower cavity Lower* ones; neither set had names."""
    from custom_components.localthings.catalog import translated_states
    from custom_components.localthings.select import _display

    _bound, _materialized, _skipped, full_resources, _name = _discover()
    known = translated_states("select", "oven_mode")
    for href in ("/mode/vs/0", "/mode/vs/1"):
        modes = full_resources[href]["x.com.samsung.da.supportedModes"]
        assert modes
        assert all(_display(mode, "oven_mode") in known for mode in modes), (href, modes)


def test_divider_sensor_is_on_the_lower_cavity_only():
    """This dump has the divider in: the upper cavity offers only Upper*
    modes and the lower cavity's /connected reads On."""
    bound, _materialized, _skipped, full_resources, _name = _discover()
    state = flatten(bound, full_resources)
    assert state["subdevice1_divider"] is True
    assert "divider" not in state
    # The one field is the divider there, not a second, connectivity sensor.
    assert "subdevice1_cloud_connected" not in state
    assert "cloud_connected" in state


def test_divider_sensor_reads_off_when_the_divider_is_out():
    """Divider out, as on an NX9802T: the lower cavity's /connected reads Off."""
    bound, _materialized, _skipped, full_resources, _name = _discover()
    connected = next(b.href for b in bound if b.desc.key == "divider" and b.subdevice.key == "1")
    assert connected == "/connected/vs/1"
    resources = {**full_resources, connected: {"x.com.samsung.da.connected": "Off"}}
    assert flatten(bound, resources)["subdevice1_divider"] is False

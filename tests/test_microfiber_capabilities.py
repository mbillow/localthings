"""Microfiber laundry-lint filter appliance (issue #475,
AMF-WW-TP1-22-COMMON). Reports washer resources it has no cycle behind, so
it has its own registry (issue #626), routed via /oic/d's
x.com.st.d.microfiberfilter: a water/microfiber filter usage counter, a
separate /filterstatus/vs/0 blockage notice, and what the filter is doing.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.capabilities import common
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SensorDesc
from tests.conftest import _load_device

FIXTURE = "microfiber"
DEVICE_TYPES = ("oic.wk.d", "x.com.st.d.microfiberfilter")


def _device():
    resources = _load_device(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    return reg, resources


def _state():
    reg, resources = _device()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_routes_to_its_own_registry_via_oic_type():
    reg = for_device_by_oic_type(DEVICE_TYPES)
    assert reg is not None and reg.name == "microfiber_filter"


def test_filter_activity_reads_the_filters_own_progress():
    """Its states come from the filter's supportedProgress, not the washer's
    cycle vocabulary (issue #626)."""
    reg, resources = _device()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    desc = next(b.desc for b in bound if b.desc.key == "filter_activity")
    assert isinstance(desc, SensorDesc)
    assert desc.options(resources) == [
        "none",
        "filtering",
        "bypassing",
        "waiting",
        "stopping",
        "sensing",
    ]
    assert _state()["filter_activity"] in desc.options(resources)


def test_no_washer_cycle_entities():
    """Issue #626: no child lock, delay, start/stop or cycle progress."""
    reg, resources = _device()
    keys = {b.desc.key for b in discover(resources, reg.capabilities, reg.pattern_capabilities)}
    assert not keys & {
        "child_lock",
        "cycle_active",
        "delay_start_hours",
        "start",
        "pause",
        "stop",
        "progress",
        "finish_time",
        "job_beginning_status",
        "diagnosis_start",
    }


def test_no_unbound_hrefs():
    reg, resources = _device()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_water_filter_usage_and_status_bound():
    state = _state()
    assert state["filter_usage"] == "4"
    assert state["filter_status"] == "normal"


def test_filter_blockage_is_a_problem_binary_sensor():
    """/filterstatus/vs/0's filterStatusNotice is a bare (non-`x.com...`)
    field; 'Normal' here reads as no problem."""
    desc = next(e for e in common.FILTER_STATUS.entities if e.key == "filter_blockage")
    assert isinstance(desc, BinarySensorDesc)
    assert desc.device_class == "problem"
    assert desc.value_fn("Blockage") is True
    assert desc.value_fn("Normal") is False
    assert _state()["filter_blockage"] is False


def test_filter_reset_button_offered():
    """The AMF filter declares filterResetType ['replaceable'], so the
    shared reset button applies here too (read-only listing until a
    reporter confirms the write on hardware)."""
    reg, resources = _device()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    # Reset is a stateless button, so it doesn't appear in flattened state;
    # assert the descriptor exists and its gate passes on this rep.
    reset = next(e for e in common.WATER_FILTER.entities if e.key == "filter_reset")
    assert reset.exists_fn is not None
    assert reset.exists_fn(resources["/filter/waterfilter/vs/0"], resources)
    assert "filter_usage" in state

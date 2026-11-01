"""Gas range NX60T8311SS/AA (TP2X_DA-KS-RANGE-0101X, issue #444): the first
TP2X range in the corpus, captured with all five burners lit so the
/cooktopmonitoring/vs/0 bitmask is non-zero (31)."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import range as range_caps
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.range")


def _range():
    resources = _load_device("range_nx60t8311ss")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    return reg, resources


def _state(resources=None):
    reg, res = _range()
    res = resources if resources is not None else res
    bound = discover(res, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, res)


def test_resolves_to_range_registry():
    reg, _ = _range()
    assert reg is not None and reg.name == "range"


def test_no_unbound_hrefs():
    reg, resources = _range()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_all_five_burners_lit_in_capture():
    state = _state()
    assert state["cooktop_running_state"] == "Run"
    assert state["active_burners"] == 5
    for n in range(1, 6):
        assert state[f"cooktop_burner_{n}"] is True


def test_mask_entities_gated_when_field_absent():
    """A board reporting the resource without the field (only
    warmingCenterState/cooktopRunningState) gets no phantom burners and no
    raw value either."""
    _, resources = _range()
    resources = dict(resources)
    rep = dict(resources["/cooktopmonitoring/vs/0"])
    del rep["x.com.samsung.da.cooktopMonitoring"]
    resources["/cooktopmonitoring/vs/0"] = rep
    state = _state(resources)
    assert "active_burners" not in state
    assert "cooktop_monitoring" not in state
    assert not any(k.startswith("cooktop_burner_") for k in state)


def test_gas_board_gets_decoded_set_and_no_raw_value():
    """This unit reports Fuel_Gas, the token the bit map was verified on:
    count plus five burners, and the raw diagnostic stays out of the way."""
    state = _state()
    assert "cooktop_monitoring" not in state
    assert state["active_burners"] == 5


def _without_fuel_token(resources):
    resources = dict(resources)
    mode = dict(resources["/mode/vs/0"])
    mode["x.com.samsung.da.options"] = [
        o for o in mode["x.com.samsung.da.options"] if not o.startswith("Fuel_")
    ]
    resources["/mode/vs/0"] = mode
    return resources


def test_board_without_fuel_token_gets_raw_value_only():
    """Every electric NE fixture reports the field (always 0 so far) and no
    Fuel_ token. Until someone confirms the bit map there, the raw value is
    the only entity: no phantom Burner 5 on a four-element board, and the
    number an owner needs to report is visible in HA."""
    _, resources = _range()
    state = _state(_without_fuel_token(resources))
    assert state["cooktop_monitoring"] == 31
    assert "active_burners" not in state
    assert not any(k.startswith("cooktop_burner_") for k in state)


def test_stub_mode_resource_keeps_the_optimistic_carve_out():
    """A /mode/vs/0 not fetched yet at discovery is treated like every
    other stub: the decoded set is created rather than hidden."""
    _, resources = _range()
    resources = dict(resources)
    resources["/mode/vs/0"] = {"href": "/mode/vs/0"}
    state = _state(resources)
    assert state["active_burners"] == 5
    assert "cooktop_monitoring" not in state


def test_mask_entities_stand_down_when_burner_list_present():
    """A board carrying both resources would otherwise list every burner
    twice. burnerList is the richer source (state, power level, timer per
    burner), so it wins and the mask entities are not created. No fixture
    has both today; this splices range_device.json's burnerList into the
    NX60T8311SS capture."""
    _, resources = _range()
    resources = dict(resources)
    resources["/cooktop/status/vs/0"] = _load_device("range")["/cooktop/status/vs/0"]
    state = _state(resources)
    assert "active_burners" not in state
    assert not any(k.startswith("cooktop_burner_") for k in state)
    assert state["cooktop_monitoring"] == 31
    assert state["cooktop_running_state"] == "Run"
    assert "burner_0_state" in state


def test_mask_entities_stay_when_burner_list_empty():
    """An empty burnerList carries no burners, so the mask is still the only
    per-burner source."""
    _, resources = _range()
    resources = dict(resources)
    resources["/cooktop/status/vs/0"] = {"burnerList": []}
    state = _state(resources)
    assert state["active_burners"] == 5


def _mask_desc(key):
    return next(e for e in range_caps.COOKTOP_MONITORING.entities if e.key == key)


@pytest.mark.parametrize(
    ("mask", "lit", "count"),
    [
        ("0", (), 0),
        ("1", (1,), 1),  # front-left alone
        ("8", (4,), 1),  # back-right alone; unchanged when turned down to simmer
        ("3", (1, 2), 2),
        ("7", (1, 2, 3), 3),
        ("23", (1, 2, 3, 5), 4),
        ("31", (1, 2, 3, 4, 5), 5),
        ("24", (4, 5), 2),
    ],
)
def test_mask_decodes_to_burners_and_count(mask, lit, count):
    """Values read live while lighting and extinguishing burners one at a
    time on the NX60T8311SS."""
    rep = {"x.com.samsung.da.cooktopMonitoring": mask}
    assert _mask_desc("active_burners").rep_fn(rep) == count
    for n in range(1, 6):
        assert _mask_desc(f"cooktop_burner_{n}").rep_fn(rep) is (n in lit)


def test_mask_unparseable_reads_unknown():
    rep = {"x.com.samsung.da.cooktopMonitoring": "n/a"}
    assert _mask_desc("active_burners").rep_fn(rep) is None
    assert _mask_desc("cooktop_burner_1").rep_fn(rep) is None


def test_the_full_dump_leaves_nothing_unbound():
    """Issue #670's dump of the same range carries 33 hrefs, including an
    empty /recipe/cook/vs/0 and a hood fan and grill areas it has no state for."""
    resources = _load_device("range_nx60t8311ss_full")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "range"
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []

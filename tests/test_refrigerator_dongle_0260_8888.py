"""The BRB260087WW behind a DONGLE_0260 8888 bridge (#728): its Doors items
carry only ids, so each reporting door gets a numbered sensor (Door 1,
Door 2, ...) alongside the aggregate, for users to rename. Described doors
are named instead; see test_refrigerator_tp6x_ref_16k_8888.py."""

import copy
import json
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http import table_for, to_resources, with_record_type
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover

FIXTURES = Path(__file__).parent / "fixtures"


def _bodies(name="refrigerator_dongle_0260_8888"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["bodies"]


def _state(bodies):
    resources = to_resources(with_record_type(bodies, bodies["type"]), table_for(bodies["type"]))
    registry = resolve(resources)
    assert registry is not None
    assert registry.name == "refrigerator"
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    return flatten(bound, resources)


def _with_open(door_id):
    bodies = copy.deepcopy(_bodies())
    for item in bodies["Doors"]:
        if item["id"] == door_id:
            item["openState"] = "Open"
    return bodies


def test_both_doors_closed():
    state = _state(_bodies())

    assert state["door_open"] is False
    assert state["door_1_open"] is False
    assert state["door_2_open"] is False


def test_only_reporting_doors_are_numbered():
    """Item "2" carries no openState, so there is no Door 3."""
    state = _state(_bodies())

    assert "door_3_open" not in state
    assert "door_4_open" not in state


@pytest.mark.parametrize(
    ("door_id", "open_key", "closed_key"),
    [
        ("0", "door_1_open", "door_2_open"),
        ("1", "door_2_open", "door_1_open"),
    ],
)
def test_each_numbered_door_reads_its_own_item(door_id, open_key, closed_key):
    state = _state(_with_open(door_id))

    assert state[open_key] is True
    assert state[closed_key] is False
    assert state["door_open"] is True


def test_temperatures_still_read_through():
    state = _state(_bodies())

    assert state["fridge_temperature"] == 4
    assert state["freezer_temperature"] == -18


def test_a_single_reporting_door_gets_only_the_aggregate():
    """The DONGLE_0281 fridge reports one door (#698); a numbered sensor
    there would duplicate door_open."""
    state = _state(_bodies("refrigerator_dongle_0281_8888"))

    assert "door_open" in state
    assert "door_1_open" not in state
    assert "door_2_open" not in state


def test_doors_follow_the_bridge_order_whatever_their_ids():
    bodies = copy.deepcopy(_bodies())
    bodies["Doors"] = [
        {"id": "7", "openState": "Open"},
        {"id": "8", "openState": "Close"},
        {"id": "9", "openState": "Open"},
    ]
    state = _state(bodies)

    assert state["door_1_open"] is True
    assert state["door_2_open"] is False
    assert state["door_3_open"] is True
    assert "door_4_open" not in state


def test_one_undescribed_door_numbers_them_all():
    """Named keys need every reporting door to have one; a mix would leave
    the undescribed door's number depending on its neighbours'."""
    bodies = copy.deepcopy(_bodies())
    bodies["Doors"][0]["description"] = "Freezer"
    state = _state(bodies)

    assert state["door_1_open"] is False
    assert state["door_2_open"] is False
    assert "door_freezer_open" not in state


def test_a_missing_door_reads_unknown_not_closed():
    """A door position the items don't fill reads unknown, not a false Closed."""
    from custom_components.localthings.registry.capabilities.fridge import DOORS_FALLBACK

    by_key = {e.key: e for e in DOORS_FALLBACK.entities}
    items = _bodies()["Doors"]
    items = [{"x.com.samsung.da.openState": i["openState"]} for i in items if "openState" in i]

    assert by_key["door_2_open"].value_fn(items) is False
    assert by_key["door_3_open"].value_fn(items) is None


def test_a_stub_doors_resource_binds_no_numbered_doors():
    """A not-yet-fetched /doors/vs/0 must not mint Door 1 / Door 2: on a
    single-door fridge they would stay forever."""
    from custom_components.localthings.registry.capabilities.fridge import DOORS_FALLBACK

    stub = {"href": "/doors/vs/0"}
    numbered = [
        e for e in DOORS_FALLBACK.entities if e.key.startswith("door_") and e.key != "door_open"
    ]

    assert numbered
    assert all(e.exists_fn is not None and not e.exists_fn(stub, {}) for e in numbered)

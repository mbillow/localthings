"""The 4-way cassette's per-side vanes (issue #704)."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

HREF = "/fourwayflaps/vs/0"
SIDES = ("north", "east", "south", "west")


def _resources():
    return _load_device("airconditioner_tp1x_sac_fourway")


def _desc(side) -> SelectDesc:
    desc = next(d for d in airconditioner.FOUR_WAY_FLAPS.entities if d.key == f"{side}_flap")
    assert isinstance(desc, SelectDesc)
    return desc


def test_nothing_is_left_unbound():
    resources = _resources()
    registry = resolve(resources, device_types=("oic.wk.d", "oic.d.airconditioner"))
    assert registry is not None
    unbound: list[str] = []

    discover(resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append)

    assert unbound == []


def test_each_side_reads_its_own_field():
    resources = _resources()
    resources[HREF] = {**resources[HREF], "x.com.samsung.da.eastFlap": "Vertical"}
    registry = resolve(resources, device_types=("oic.wk.d", "oic.d.airconditioner"))
    assert registry is not None

    state = flatten(discover(resources, registry.capabilities), resources)

    assert {side: state[f"{side}_flap"] for side in SIDES} == {
        "north": "Stop",
        "east": "Vertical",
        "south": "Stop",
        "west": "Stop",
    }


@pytest.mark.parametrize("side", SIDES)
def test_a_side_writes_only_its_own_field(side):
    write = _desc(side).write_fn
    assert write is not None

    assert write("Vertical", _resources()[HREF]) == (
        ["fourwayflaps", "vs", "0"],
        {f"x.com.samsung.da.{side}Flap": "Vertical"},
    )


def test_a_value_the_unit_does_not_list_is_not_written():
    write = _desc("north").write_fn
    assert write is not None

    assert write("Horizontal", _resources()[HREF]) is None


def test_a_unit_listing_no_supported_modes_gets_no_select():
    exists = _desc("north").exists_fn
    assert exists is not None

    assert exists({"x.com.samsung.da.northFlap": "Stop"}, {}) is False
    assert exists(_resources()[HREF], {}) is True

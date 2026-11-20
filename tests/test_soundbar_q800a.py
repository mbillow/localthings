"""HW-Q800A channel trims when the resource-wide support flag is absent."""

from copy import deepcopy

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")
HREF = "/sec/networkaudio/channelVolume"
FIELD = "x.com.samsung.networkaudio.channelVolume"
FLAG = "x.com.samsung.networkaudio.isSupported"


def _discover(resources):
    registry = resolve(resources, device_types=DEVICE_TYPES)
    assert registry is not None and registry.name == "soundbar"
    unbound = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    assert unbound == []
    return bound


def test_q800a_missing_flag_exposes_only_advertised_channel_trims():
    resources = _load_device("soundbar_q800a")
    assert FLAG not in resources[HREF]
    bound = _discover(resources)
    assert flatten(bound, resources) == {
        "channel_level_center": 0,
        "channel_level_front_top": 0,
        "channel_level_rear": 3,
        "channel_level_rear_top": 3,
        "rear_speakers": True,
    }


@pytest.mark.parametrize(
    "key,wire",
    [
        ("channel_level_rear", "Spk_Rear"),
        ("channel_level_rear_top", "Spk_Rear_Top"),
    ],
)
def test_q800a_rear_sliders_keep_bounds_and_write_only_requested_channel(key, wire):
    resources = _load_device("soundbar_q800a")
    bound = next(b for b in _discover(resources) if b.desc.key == key)
    desc = bound.desc
    assert isinstance(desc, NumberDesc)
    assert desc.exists_fn is not None and desc.exists_fn(resources[HREF], resources)
    assert (desc.native_min, desc.native_max, desc.step, desc.entity_category) == (
        -6,
        6,
        1,
        "config",
    )
    assert desc.write_fn is not None
    assert desc.write_fn(-2, resources[HREF], bound.href) == (
        ["sec", "networkaudio", "channelVolume"],
        {FIELD: [{"name": wire, "value": -2}]},
    )


@pytest.mark.parametrize("flag", [None, 1, 0])
@pytest.mark.parametrize("status", [-1, 0, 1])
def test_channel_support_rows_and_explicit_resource_flag(flag, status):
    resources = deepcopy(_load_device("soundbar_q800a"))
    resources[HREF][FIELD] = [{"name": "Spk_Rear", "status": status, "value": 3}]
    if flag is not None:
        resources[HREF][FLAG] = flag
    state = flatten(_discover(resources), resources)
    assert ("channel_level_rear" in state) is (status != -1 and flag != 0)
    assert state["rear_speakers"] is (status == 1)


def test_missing_channel_row_does_not_create_a_trim():
    resources = _load_device("soundbar_q800a")
    resources[HREF][FIELD] = []
    assert flatten(_discover(resources), resources) == {"rear_speakers": False}

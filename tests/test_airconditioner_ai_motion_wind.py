"""AI motion wind on the TP1X_DA-AC-RAC-01011 board (issue #554,
AR80H12CAAWNSK)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

NAME = "airconditioner_tp1x_rac_01011_aimotionwind"
DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _discover(resources, log=None):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "airconditioner"
    return discover(resources, reg.capabilities, reg.pattern_capabilities, log=log)


def test_binds_everything():
    unbound = []
    _discover(_load_device(NAME), log=unbound.append)
    assert unbound == []


def test_select_reads_the_mode_and_the_devices_own_options():
    resources = _load_device(NAME)
    bound = _discover(resources)
    select = next(b for b in bound if b.desc.key == "ai_motion_wind")
    assert select.desc.options_field == "supportedModes"
    assert "AiIndirect" in resources["/aimotionwind/vs/0"]["supportedModes"]
    assert flatten(bound, resources)["ai_motion_wind"] == "Off"


def test_select_writes_the_raw_mode_code():
    resources = _load_device(NAME)
    select = next(b for b in _discover(resources) if b.desc.key == "ai_motion_wind")
    rep = resources["/aimotionwind/vs/0"]
    assert select.desc.write_fn("WindFree", rep) == (
        ["aimotionwind", "vs", "0"],
        {"mode": "WindFree"},
    )

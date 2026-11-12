"""Full redacted QN90A capture routes through the shared Tizen profile (#755)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import registry_for, resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.tv")


def test_tv_routes_on_its_type_and_keeps_existing_families():
    resources = _load_device("tv_qn90a")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "tv"
    assert resolve(resources) is None
    assert registry_for("monitor").name == "monitor"
    assert registry_for("soundbar").name == "soundbar"
    assert reg.subscribe_tiers == ()


def test_full_capture_reuses_plumbing_without_hiding_unsupported_controls():
    resources = _load_device("tv_qn90a")
    reg = registry_for("tv")
    gaps = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=gaps.append)
    assert sorted(gaps) == [
        "/capability/weatherContents/main/0",
        "/sec/tv/launchnativeapp",
        "/sec/tv/remotecontrol",
        "/sec/tv/switch/ambient",
    ]
    assert set(flatten(bound, resources)) == {"media_player", "network_connection", "picture_mode"}
    selects = [b.desc for b in bound if isinstance(b.desc, SelectDesc)]
    assert len(selects) == 1 and selects[0].key == "picture_mode"
    assert selects[0].control_response and not selects[0].confirm_on_refusal

"""HW-Q935GF soundbar (#853): a 2025 bar that serves auto power down, the TV
remote setting and the player info under /sec/networkaudio/av/."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")


def _bound():
    resources = _load_device("soundbar_q935gf")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "soundbar"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, resources, unbound


def test_no_unbound_hrefs():
    _, _, unbound = _bound()
    assert unbound == []


def test_av_settings_read_back():
    bound, resources, _ = _bound()
    state = flatten(bound, resources)
    assert state["auto_power_down"] is True
    assert state["tv_remote"] == "TV_REMOTE_OFF"
    assert state["audio_codec"] == "WIFI"
    assert state["ip_control"] is True


def test_tv_remote_writes_the_raw_code_to_its_own_href():
    bound, _, _ = _bound()
    (tv_remote,) = [b for b in bound if b.desc.key == "tv_remote"]
    desc = tv_remote.desc
    assert isinstance(desc, SelectDesc) and desc.write_fn is not None
    assert desc.write_fn("TV_REMOTE_SAMSUNG", {}, tv_remote.href) == (
        ["sec", "networkaudio", "av", "tvremote"],
        {"currentRemote": "TV_REMOTE_SAMSUNG"},
    )


def test_tv_remote_hidden_when_the_current_value_is_not_advertised():
    bound, resources, _ = _bound()
    (tv_remote,) = [b for b in bound if b.desc.key == "tv_remote"]
    exists = tv_remote.desc.exists_fn
    assert exists is not None
    assert exists(resources[tv_remote.href], resources)
    assert not exists({"currentRemote": "TV_REMOTE_OFF", "supportedRemote": []}, resources)

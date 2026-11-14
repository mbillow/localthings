"""HW-Q700C soundbar (#796): its media resources sit under
/sec/networkaudio/av/ rather than /capability/*/main/0."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import av, soundbar
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import MediaPlayerDesc
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")


def _bound():
    resources = _load_device("soundbar_q700c")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "soundbar"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, resources, unbound


def test_no_unbound_hrefs():
    _, _, unbound = _bound()
    assert unbound == []


def test_one_media_player_on_the_networkaudio_layout():
    bound, resources, _ = _bound()
    players = [b for b in bound if isinstance(b.desc, MediaPlayerDesc)]
    assert [b.href for b in players] == ["/sec/networkaudio/av/mediaPlayback"]
    desc = players[0].desc
    assert desc.track_href == "/sec/networkaudio/av/audioTrackData"
    assert desc.shuffle_href == "/sec/networkaudio/av/mediaPlaybackShuffle"
    assert flatten(bound, resources)["media_player"] == "play"


def test_media_writes_target_the_bars_own_layout():
    desc = soundbar.MEDIA_PLAYERS[1].entities[0]
    assert isinstance(desc, MediaPlayerDesc) and desc.write_fn is not None
    assert desc.write_fn(("playback", "pause"), {}) == (
        ["sec", "networkaudio", "av", "mediaPlayback"],
        {"modes": ["pause"]},
    )
    assert desc.write_fn(("next_track", None), {}) == (
        ["sec", "networkaudio", "av", "mediaTrackControl"],
        {"modes": ["next"]},
    )
    # Volume and source live on the shared networkaudio hrefs either way.
    assert desc.write_fn(("volume", 20), {}) == (
        ["sec", "networkaudio", "audio"],
        {"volume": 20},
    )
    assert av.media_write("shuffle", True) == (
        ["capability", "mediaPlaybackShuffle", "main", "0"],
        {"mode": "enabled"},
    )


def test_s61b_layout_keeps_one_media_player():
    resources = _load_device("soundbar_s61b")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    players = [b.href for b in bound if isinstance(b.desc, MediaPlayerDesc)]
    assert players == [av.HREF_MEDIA_PLAYBACK]

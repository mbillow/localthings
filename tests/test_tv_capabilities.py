"""UE50TU7172 TV (oic.d.tv).

Routed by /oic/d alone, like the soundbar. Mode resources are {name, id}
lists: entities show the localized names, writes send the ids.
"""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import (
    for_device_by_oic_type,
    registry_for,
    resolve,
)
from custom_components.localthings.registry.capabilities import tv
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    MediaPlayerDesc,
    RemoteDesc,
    SelectDesc,
    SensorDesc,
)
from tests.conftest import _load_device

FIXTURE = "tv_ue50tu7172"
DEVICE_TYPES = ("oic.wk.d", "oic.d.tv")


def _tv():
    resources = _load_device(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    return reg, resources


def _state():
    reg, resources = _tv()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_via_oic_type_alone():
    reg = for_device_by_oic_type(DEVICE_TYPES)
    assert reg is not None and reg.name == "tv"
    assert resolve(_load_device(FIXTURE)) is None


def test_no_unbound_hrefs():
    reg, resources = _tv()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_modes_show_names_not_ids():
    """The state is the mode id; the select shows the TV's localized name."""
    _, resources = _tv()
    assert _state()["picture_mode"] == "modeStandard"
    desc = next(e for e in tv.PICTURE_MODE.entities if isinstance(e, SelectDesc))
    assert desc.display_fn is not None
    assert desc.display_fn("modeStandard", resources) == "Standard"


def test_mode_options_come_from_the_device():
    reg, resources = _tv()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    sel = next(b for b in bound if b.desc.key == "picture_mode")
    assert isinstance(sel.desc, SelectDesc)
    options = sel.desc.options(resources)
    labels = tv.mode_options(resources[tv.HREF_PICTURE])
    assert sorted(labels[option] for option in options) == [
        "Dynamic",
        "FILMMAKER MODE",
        "Movie",
        "Natural",
        "Standard",
    ]


def test_mode_write_carries_the_id_back():
    desc = next(e for e in tv.PICTURE_MODE.entities if e.key == "picture_mode")
    rep = {
        "if": ["oic.if.a", "oic.if.baseline"],
        "x.com.samsung.tv.supportedModes": [
            {"x.com.samsung.tv.name": "Movie", "x.com.samsung.tv.id": "modeMovie"},
            {"x.com.samsung.tv.name": "Standard", "x.com.samsung.tv.id": "modeStandard"},
        ],
    }
    assert isinstance(desc, SelectDesc)
    assert desc.write_fn is not None
    assert tv._mode_write("modeMovie", rep, tv.HREF_PICTURE) == (
        ["sec", "tv", "mode", "picture"],
        {"x.com.samsung.tv.currentMode": "modeMovie"},
    )
    # A mode the TV doesn't list is not written.
    assert tv._mode_write("Nonsense", rep, tv.HREF_PICTURE) is None


def test_media_player_is_the_only_power_volume_source_surface():
    state = _state()
    for dup in (
        "power_switch",
        "volume",
        "mute",
        "input_source",
        "tv_sound_mode",
        "repeat",
    ):
        assert dup not in state
    # Bound to the power switch; the platform reads playback from the snapshot.
    assert state["media_player"] == "on"

    assert isinstance(tv.MEDIA_PLAYER.entities[0], MediaPlayerDesc)
    write = tv._media_write
    resources = _load_device(FIXTURE)
    assert write(("power", False), {}, None, resources) == (
        ["sec", "tv", "switch", "binary"],
        {"value": False},
    )
    assert write(("volume", 10), {}, None, resources) == (["sec", "tv", "audio"], {"volume": 10})
    assert write(("mute", True), {}, None, resources) == (["sec", "tv", "audio"], {"mute": True})
    # Turn-on is Wake-on-LAN (av.py); OCF can't reach a TV in standby.
    assert write(("power", True), {}, None, resources) is None


def test_media_player_writes_resolve_names_and_commands():
    write = tv._media_write
    resources = _load_device(FIXTURE)
    assert write(("source", "HDMI1"), {}, None, resources) == (
        ["sec", "tv", "inputsource"],
        {"x.com.samsung.tv.currentMode": "HDMI1"},
    )
    assert write(("sound_mode", "modeExternalGame"), {}, None, resources) == (
        ["sec", "tv", "mode", "sound"],
        {"x.com.samsung.tv.currentMode": "modeExternalGame"},
    )
    # A name, or a mode the TV doesn't list, is not written.
    assert write(("source", "HDMI 1"), {}, None, resources) is None
    assert write(("next_track", None), {}, None, resources) == (
        ["capability", "mediaTrackControl", "main", "0"],
        {"modes": ["next"]},
    )
    # A relative offset in milliseconds, as on the soundbar.
    assert write(("seek", 15000), {}, None, resources) == (
        ["capability", "mediaPlayback", "main", "0"],
        {"modes": ["seek"], "position": 15000},
    )


def test_remote_writes_plugin_proven_fields():
    desc = next(e for e in tv.REMOTE.entities if isinstance(e, RemoteDesc))
    assert desc.write_fn is not None
    assert desc.write_fn("KEY_CHUP", {}) == (
        ["sec", "tv", "remotecontrol"],
        {"x.com.samsung.tv.keyvalue": "KEY_CHUP", "x.com.samsung.tv.keystatus": "pressAndRelease"},
    )


def test_open_app_launches_a_known_app_by_name():
    """Measured: an app launches by targetAppId, a built-in screen by targetAppName."""
    from custom_components.localthings import tvapps

    desc = next(e for e in tv.OPEN_APP.entities if isinstance(e, SelectDesc))
    assert desc.write_fn is not None and desc.rep_fn is not None
    rep = {
        "x.com.samsung.tv.supportedNativeApps": ["home", "guide"],
        tvapps.FIELD: {"apps": {"kIciSQlYEM.plex": "Plex"}, "current": "kIciSQlYEM.plex"},
    }
    assert desc.options({tvapps.LAUNCH_HREF: rep}) == ["home", "guide", "Plex"]
    assert desc.rep_fn(rep) == "Plex"
    assert desc.write_fn("Plex", rep) == (
        ["sec", "tv", "launchapp"],
        {"x.com.samsung.tv.targetAppId": "kIciSQlYEM.plex"},
    )
    assert desc.write_fn("guide", rep) == (
        ["sec", "tv", "launchapp"],
        {"x.com.samsung.tv.targetAppName": "guide"},
    )


def test_channel_shows_the_name_and_keeps_the_number():
    state = _state()
    assert state["channel"] == "News 24 HD"
    # The number is an attribute; only the state is checked here.
    desc = next(e for e in tv.CHANNEL.entities if isinstance(e, SensorDesc))
    rep_fn = desc.rep_fn
    assert rep_fn is not None
    rep = {"x.com.samsung.tv.channelNumber": "4", "x.com.samsung.tv.channelName": "News 24 HD"}
    assert rep_fn(rep) == "News 24 HD"
    assert rep_fn({"x.com.samsung.tv.channelNumber": "4"}) == "4"


def test_network_connection_reports_the_class_not_the_ssid():
    from custom_components.localthings.registry.capabilities.av import _connection_kind

    assert _connection_kind({"tnn": "Ethernet"}) == "ethernet"
    assert _connection_kind({"tnn": "ExampleWiFi"}) == "wifi"
    assert _connection_kind({}) is None


def test_now_showing_thumbnail_path_emitted_only_when_present():
    desc = next(iter(tv.CONTENT_INFO.entities))
    attrs = desc.extra_state_attributes_fn
    assert attrs is not None
    base = {"x.com.samsung.tv.title": "Evening News", "x.com.samsung.tv.thumbnailpath": ""}
    assert "thumbnail_path" not in attrs(base, {})
    with_art = {**base, "x.com.samsung.tv.thumbnailpath": "https://cdn.example/p.jpg"}
    assert attrs(with_art, {})["thumbnail_path"] == "https://cdn.example/p.jpg"


def test_now_showing_carries_the_broadcast():
    state = _state()
    assert state["now_showing"] == "Evening News"
    assert "firmware" not in state


def test_attitude_toward_the_board_is_deliberate():
    """Same board class as the soundbar: poll-only, fast write-settle."""
    from custom_components.localthings.registry.by_type import registry_for

    tv = registry_for("tv")
    assert tv.subscribe_tiers == ()
    assert tv.write_settle_s == 6.0


def test_firmware_falls_back_to_the_ocf_resource_version_build():
    from custom_components.localthings.registry.identity import (
        DeviceIdentity,
        resolve_firmware,
    )

    resources = _load_device(FIXTURE)
    assert resolve_firmware(None, resources) == "T-KTSU2DEUC-2743.0"
    ident = DeviceIdentity(
        manufacturer="", model="", name="", serial=None, raw={"/oic/p": {"mnfv": "X-1.0"}}
    )
    assert resolve_firmware(ident, resources) == "X-1.0"
    assert resolve_firmware(None, {}) is None


def test_coverage_only_resources_are_not_re_read_every_sweep():
    from custom_components.localthings.registry.by_type.tv import REGISTRY

    tiers = {href: {cap.poll_tier for cap in caps} for href, caps in REGISTRY.capabilities.items()}
    assert tiers["/sec/tv/deviceinfo"] == {"never"}
    assert tiers["/sec/provisioninginfo"] == {"never"}
    assert tiers["/capability/videoMetaData/main/0"] == {"hot"}  # the media player reads it
    assert all(cap.poll_tier == "never" for cap in REGISTRY.pattern_capabilities)


# The QE50QN90A capture (#755).


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
    assert set(flatten(bound, resources)) == {
        "channel",
        "media_player",
        "network_connection",
        "now_showing",
        "open_app",
        "picture_mode",
    }
    picture = next(
        b.desc for b in bound if isinstance(b.desc, SelectDesc) and b.desc.key == "picture_mode"
    )
    assert picture.control_response and not picture.confirm_on_refusal


def test_tv_overrides_are_explicit_replacements_of_the_monitor_surface():
    """by_type/tv.py swaps capabilities in by href; a replacement for an href
    the monitor no longer binds fails at import rather than binding twice."""
    from custom_components.localthings.registry.by_type._base import replacing
    from custom_components.localthings.registry.by_type.monitor import REGISTRY as MONITOR
    from custom_components.localthings.registry.by_type.tv import REGISTRY as TV
    from custom_components.localthings.registry.capability import Capability

    for cap in tv.TV_REPLACES:
        assert cap.href in MONITOR.capabilities
        assert TV.capabilities[cap.href] == [cap]
    with pytest.raises(ValueError, match="/sec/tv/nothing"):
        replacing([tv.PICTURE_MODE], [Capability(href="/sec/tv/nothing")])


def test_the_uuid_nested_light_sensor_copy_is_covered_without_a_second_entity():
    """ViewFinity S9 (#891): the light sensor's own SmartThings device repeats
    /light/sensor/brightness/vs/0 under a per-unit UUID. The unprefixed copy
    is the illuminance sensor; the nested one binds nothing."""
    resources = _load_device("monitor_ls27c902p")
    reg = registry_for("monitor")
    gaps: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=gaps.append)
    assert gaps == []
    nested = [h for h in resources if h.startswith("/00000000-")]
    assert len(nested) == 2
    assert not [b for b in bound if b.href in nested]
    assert [b.href for b in bound if b.desc.key == "illuminance"] == [
        "/light/sensor/brightness/vs/0"
    ]

"""HW-S61B soundbar (oic.d.networkaudio).

Routed by /oic/d alone; the fixture is what registry/flat.py's per-href
reads produce.
"""

from typing import Any

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import (
    for_device_by_oic_type,
    registry_for,
    resolve,
)
from custom_components.localthings.registry.capabilities import soundbar
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    ButtonDesc,
    NumberDesc,
    SelectDesc,
)
from tests.conftest import _load_device

FIXTURE = "soundbar_s61b"
DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")


def _bar():
    resources = _load_device(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    return reg, resources


def _write(desc, payload, href: str | None):
    """Call write_fn the way the coordinator does, with the bound href."""
    write: Any = desc.write_fn
    return write(payload, {}, href)


def _state():
    reg, resources = _bar()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_via_oic_type_alone():
    """A soundbar's model strings don't exist; only /oic/d's rt can route it."""
    reg = for_device_by_oic_type(DEVICE_TYPES)
    assert reg is not None and reg.name == "soundbar"
    # Without /oic/d the same resources resolve to nothing.
    assert resolve(_load_device(FIXTURE)) is None


def test_no_unbound_hrefs():
    reg, resources = _bar()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_media_surface_resources_are_covered_without_entities():
    """Power, volume, input and playback stay bound but get no entities yet."""
    state = _state()
    for key in (
        "power_switch",
        "volume",
        "mute",
        "input_source",
        "soundbar_sound_mode",
        "playback",
        "media_player",
    ):
        assert key not in state


def test_bass_boost_hidden_when_the_mode_disables_it():
    """-1 in this capture (standard mode) hides the toggle."""
    desc = next(e for e in soundbar.ADVANCED_AUDIO.entities if e.key == "bass_boost")
    assert desc.exists_fn is not None
    rep = {"x.com.samsung.networkaudio.bassboost": -1}
    assert desc.exists_fn(rep, {}) is False
    assert desc.exists_fn({"x.com.samsung.networkaudio.bassboost": 0}, {}) is True
    assert "bass_boost" not in _state()


def test_channel_levels_gate_on_presence_and_write_single_item_lists():
    state = _state()
    # Present on this unit (status 1 in the fixture): center/side/rear.
    expected = {"channel_level_center": -6, "channel_level_side": -6, "channel_level_rear": 6}
    for key, value in expected.items():
        assert state[key] == value
    # Not present (status -1) -- no entities at all.
    absent = (
        "channel_level_wide",
        "channel_level_front_top",
        "channel_level_rear_top",
        "channel_level_rear_side",
    )
    for key in absent:
        assert key not in state

    desc = next(
        e
        for e in soundbar.CHANNEL_LEVEL.entities
        if e.key == "channel_level_rear" and isinstance(e, NumberDesc)
    )
    rep_fn = desc.rep_fn
    assert rep_fn is not None
    rep = {
        "x.com.samsung.networkaudio.isSupported": 1,
        "x.com.samsung.networkaudio.channelVolume": [
            {"name": "Spk_Center", "value": -6, "status": 1},
            {"name": "Spk_Rear", "value": 6, "status": 1},
            {"name": "Spk_Wide", "value": 0, "status": -1},
        ],
    }
    assert rep_fn(rep) == 6
    assert desc.write_fn is not None
    # A one-row list, as the plugin sends.
    assert desc.write_fn(3, rep) == (
        ["sec", "networkaudio", "channelVolume"],
        {"x.com.samsung.networkaudio.channelVolume": [{"name": "Spk_Rear", "value": 3}]},
    )


def test_pair_bluetooth_posts_the_plugin_proven_style():
    """The pairing button writes pairingMode on the same resource."""
    desc = next(
        e
        for e in soundbar.BT_PAIRING.entities
        if e.key == "pair_bluetooth" and isinstance(e, ButtonDesc)
    )
    assert desc.write_fn is not None
    assert _write(desc, True, soundbar.BT_PAIRING.href) == (
        ["sec", "networkaudio", "btPairingMode"],
        {"x.com.samsung.networkaudio.pairingMode": True},
    )


def test_equalizer_write_carries_mode_and_action():
    desc = next(e for e in soundbar.EQ.entities if isinstance(e, SelectDesc))
    assert desc.write_fn is not None
    assert desc.write_fn("POP", {}) == (
        ["sec", "networkaudio", "eq"],
        {
            "x.com.samsung.networkaudio.EQname": "POP",
            "x.com.samsung.networkaudio.action": "setEQmode",
        },
    )


def test_eq_bands_read_and_write_the_whole_list():
    state = _state()
    assert all(state[f"eq_band_{i}"] == 0 for i in range(1, 8))
    desc = next(
        e for e in soundbar.EQ.entities if e.key == "eq_band_3" and isinstance(e, NumberDesc)
    )
    rep = {"x.com.samsung.networkaudio.EQband": ["0", "1", "2", "3", "4", "5", "6"]}
    assert desc.write_fn is not None
    assert desc.write_fn(-4, rep) == (
        ["sec", "networkaudio", "eq"],
        {
            "x.com.samsung.networkaudio.EQname": "NONE",
            "x.com.samsung.networkaudio.EQband": ["0", "1", "-4", "3", "4", "5", "6"],
            "x.com.samsung.networkaudio.action": "setEQvalue",
        },
    )


def test_paired_but_sleeping_rears_keep_their_trim():
    """Status 0 (pair asleep) keeps the entity; only -1 hides it."""
    desc = next(
        e
        for e in soundbar.CHANNEL_LEVEL.entities
        if e.key == "channel_level_rear" and isinstance(e, NumberDesc)
    )
    rep_fn = desc.rep_fn
    assert rep_fn is not None
    rep_sleeping = {
        "x.com.samsung.networkaudio.isSupported": 1,
        "x.com.samsung.networkaudio.channelVolume": [
            {"name": "Spk_Rear", "value": 6, "status": 0},
        ],
    }
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep_sleeping, {}) is True
    assert rep_fn(rep_sleeping) == 6


def test_rear_speakers_flag_follows_the_channel_flags():
    desc = next(e for e in soundbar.CHANNEL_LEVEL.entities if e.key == "rear_speakers")
    rep_with = {
        "x.com.samsung.networkaudio.channelVolume": [
            {"name": "Spk_Rear", "status": 1},
            {"name": "Spk_Wide", "status": -1},
        ]
    }
    items = rep_with["x.com.samsung.networkaudio.channelVolume"]
    rep_without = {"x.com.samsung.networkaudio.channelVolume": items[1:]}
    rep_fn = desc.rep_fn
    assert rep_fn is not None
    assert rep_fn(rep_with) is True
    assert rep_fn(rep_without) is False


def test_woofer_level_is_hidden_without_a_paired_sub():
    """No woofer level without a paired sub (connection 'off')."""
    desc = next(e for e in soundbar.WOOFER.entities if e.key == "woofer_level")
    assert desc.exists_fn is not None
    assert desc.exists_fn({"x.com.samsung.networkaudio.connection": "off"}, {}) is False
    assert desc.exists_fn({"x.com.samsung.networkaudio.connection": "on"}, {}) is True
    assert "woofer_level" not in _state()


def test_woofer_range_matches_the_plugin_slider():
    """Lifestyleaudio's WooferRange is asymmetric: -12..+6."""
    desc = next(
        e for e in soundbar.WOOFER.entities if e.key == "woofer_level" and isinstance(e, NumberDesc)
    )
    assert (desc.native_min, desc.native_max) == (-12, 6)


def test_woofer_and_tone_writes():
    for cap, key, path, field in (
        (soundbar.TONE, "bass", ["sec", "networkaudio", "tone"], "x.com.samsung.networkaudio.bass"),
        (
            soundbar.TONE,
            "treble",
            ["sec", "networkaudio", "tone"],
            "x.com.samsung.networkaudio.treble",
        ),
        (
            soundbar.WOOFER,
            "woofer_level",
            ["sec", "networkaudio", "woofer"],
            "x.com.samsung.networkaudio.woofer",
        ),
    ):
        desc = next(e for e in cap.entities if e.key == key)
        assert isinstance(desc, NumberDesc)
        assert desc.write_fn is not None
        assert _write(desc, 3, cap.href) == (path, {field: 3})
        # Without the href there is nowhere to write.
        assert _write(desc, 3, None) is None


def test_diagnostics_carry_the_static_strings():
    state = _state()
    assert state["speaker_status"] == "normal"
    assert state["micom_version"] == "39"
    assert state["woofer_connection"] is False


def test_wifi_signal_is_a_percent_with_the_index_kept():
    state = _state()
    # rssi 3 = 75 %; the raw index is an attribute.
    assert state["wifi_signal"] == 75


def test_attitude_toward_the_board_is_deliberate():
    """Flat, no OBSERVE and a short write-settle window (see by_type/soundbar.py)."""
    reg = registry_for("soundbar")
    assert reg.flat is True
    assert reg.subscribe_tiers == ()
    assert reg.write_settle_s == 6.0

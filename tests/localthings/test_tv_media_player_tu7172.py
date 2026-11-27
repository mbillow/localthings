"""UE50TU7172 TV: end-to-end through Home Assistant.

A PSK entry polled flat, routed by /oic/d's oic.d.tv, with the media player
carrying the TV profile: TV device class, next/previous, and mode writes
sending the ids behind the device's localized names.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES
from tests.localthings.test_soundbar import (
    ENTRY_DATA as _SOUNDBAR_ENTRY_DATA,
)
from tests.localthings.test_soundbar import (
    FakeSoundbarSession,
)

MOCK_TV_DI = "8c2f1d9e-3b55-4e7a-a211-5d9f3c6b1e42"

ENTRY_DATA = {
    **_SOUNDBAR_ENTRY_DATA,
    "device_key": MOCK_TV_DI,
    "ocf_device_id": MOCK_TV_DI,
    "model": "UE50TU7172UXXH",
}


def _tv_resources() -> dict:
    data = json.loads((FIXTURES / "tv_ue50tu7172_device.json").read_text())
    return parse_device0_batch(data["device0"])


_IDENTITY = DeviceIdentity(
    manufacturer="Samsung Electronics",
    model="UE50TU7172UXXH",
    name="Samsung 7 Series (50)",
    serial=None,
    device_id=MOCK_TV_DI,
    device_types=("oic.wk.d", "oic.d.tv"),
    raw={"/oic/res": [{"href": href} for href in _tv_resources()]},
)


class FakeTvSession(FakeSoundbarSession):
    def __init__(self) -> None:
        self.resources = _tv_resources()
        self.reads: list[str] = []
        self.writes: list[tuple[list[str], dict]] = []


async def _setup_tv(hass: HomeAssistant, entry: MockConfigEntry) -> FakeTvSession:
    session = FakeTvSession()
    with (
        patch(
            "custom_components.localthings.coordinator.create_transport",
            return_value=session,
        ),
        patch(
            "custom_components.localthings.coordinator.read_identity",
            return_value=_IDENTITY,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return session


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_TV_DI}", version=4
    )
    entry.add_to_hass(hass)
    return entry


async def test_tv_entry_polls_flat_and_routes(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._flat is True
    assert coordinator.device_type_name == "tv"


async def test_tv_media_player_profile(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)

    players = hass.states.async_all("media_player")
    assert len(players) == 1
    player = players[0]
    entity_id = player.entity_id

    assert player.state == MediaPlayerState.PLAYING
    assert player.attributes["device_class"] == MediaPlayerDeviceClass.TV
    assert player.attributes["volume_level"] == 0.10
    assert player.attributes["source"] == "TV"
    assert player.attributes["source_list"] == ["TV", "HDMI 1", "HW-S61B"]
    assert player.attributes["sound_mode"] == "Music"
    assert "Game" in player.attributes["sound_mode_list"]
    assert player.attributes["media_channel"] == "4 - News 24 HD"
    # On plain DTV the title comes from content_info.
    assert player.attributes["media_title"] == "Evening News"
    assert player.attributes["media_duration"] == 50 * 60
    assert player.attributes["media_position"] == 41 * 60
    assert player.attributes["media_position_updated_at"] is not None

    async def call(service: str, data: dict | None = None) -> None:
        await hass.services.async_call(
            "media_player", service, {"entity_id": entity_id, **(data or {})}, blocking=True
        )

    await call("select_source", {"source": "HDMI 1"})
    await call("select_sound_mode", {"sound_mode": "Game"})

    assert (
        ["sec", "tv", "inputsource"],
        {"x.com.samsung.tv.currentMode": "HDMI1"},
    ) in session.writes
    assert (
        ["sec", "tv", "mode", "sound"],
        {"x.com.samsung.tv.currentMode": "modeExternalGame"},
    ) in session.writes


async def test_tv_remote_send_command(hass: HomeAssistant) -> None:
    """One remote entity, commands at firmware names or friendly aliases."""
    from homeassistant.exceptions import ServiceValidationError

    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    remotes = hass.states.async_all("remote")
    assert len(remotes) == 1
    # Mirrors the power switch instead of "unknown".
    assert remotes[0].state == "on"
    entity_id = remotes[0].entity_id

    await hass.services.async_call(
        "remote",
        "send_command",
        {
            "entity_id": entity_id,
            "command": ["guide", "KEY_CHUP", "7"],
            "num_repeats": 2,
        },
        blocking=True,
    )

    payloads = [body for (_path, body) in session.writes]
    assert (
        payloads.count(
            {
                "x.com.samsung.tv.keyvalue": "KEY_GUIDE",
                "x.com.samsung.tv.keystatus": "pressAndRelease",
            }
        )
        == 2
    )
    assert (
        payloads.count(
            {
                "x.com.samsung.tv.keyvalue": "KEY_CHUP",
                "x.com.samsung.tv.keystatus": "pressAndRelease",
            }
        )
        == 2
    )
    assert (
        payloads.count(
            {"x.com.samsung.tv.keyvalue": "KEY_7", "x.com.samsung.tv.keystatus": "pressAndRelease"}
        )
        == 2
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "remote",
            "send_command",
            {"entity_id": entity_id, "command": ["destroy the bedroom"]},
            blocking=True,
        )


async def test_tv_standalone_entities_register(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    keys = {b.desc.key for b in coordinator.bound}
    for expected in (
        "picture_mode",
        "channel",
        "now_showing",
        "media_player",
        "cast_session",
        "cast_item",
        "remote",
        "open_app",
        "network_connection",
    ):
        assert expected in keys
    # The media player's surfaces have no standalone duplicates.
    for dup in (
        "power_switch",
        "volume",
        "mute",
        "input_source",
        "tv_sound_mode",
        "repeat",
    ):
        assert dup not in keys


async def test_broadcast_position_timestamp_tracks_the_schedule(
    hass: HomeAssistant,
) -> None:
    """On live TV the position timestamp follows the schedule, not the idle playback feed."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    async def _wait_stamp_change(previous):
        for _ in range(100):
            stamp = player.media_position_updated_at
            if stamp is not None and stamp != previous:
                return stamp
            await asyncio.sleep(0.01)
        return player.media_position_updated_at

    await coordinator.async_refresh()
    for _ in range(100):
        if player.media_position_updated_at is not None:
            break
        await asyncio.sleep(0.01)
    stamp = player.media_position_updated_at
    assert stamp is not None
    assert player.media_position == 41 * 60

    # Broadcast clock advances one minute; the idle playback feed does not.
    session.resources["/sec/tv/contentinfo"].update(
        {"x.com.samsung.tv.currenttime": "2026-01-01 19:42"}
    )
    await coordinator.async_refresh()
    for _ in range(100):
        if (rep := coordinator.resource("/sec/tv/contentinfo")) and rep.get(
            "x.com.samsung.tv.currenttime"
        ) == "2026-01-01 19:42":
            break
        await asyncio.sleep(0.01)
    new_stamp = await _wait_stamp_change(stamp)
    assert new_stamp is not None and new_stamp > stamp
    assert player.media_position == 42 * 60


async def test_remote_power_reuses_the_deterministic_write(
    hass: HomeAssistant,
) -> None:
    """The remote's off writes the power switch, never KEY_POWER; without a
    MAC to wake, on is refused rather than toggled blind (#834)."""
    from homeassistant.exceptions import ServiceValidationError

    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    remote_id = hass.states.async_all("remote")[0].entity_id

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("remote", "turn_on", {"entity_id": remote_id}, blocking=True)
    await hass.services.async_call("remote", "turn_off", {"entity_id": remote_id}, blocking=True)
    assert (["sec", "tv", "switch", "binary"], {"value": False}) in session.writes
    assert all(
        body.get("x.com.samsung.tv.keyvalue") != "KEY_POWER" for _path, body in session.writes
    )


@pytest.mark.parametrize(
    ("commands", "repeats", "expected"),
    [
        (["home"], 1, ["KEY_HOME"]),
        (["home"], 2, ["KEY_HOME", 2.0, "KEY_HOME"]),
        (["home", "right", "ok"], 1, ["KEY_HOME", 2.0, "KEY_RIGHT", 2.0, "KEY_ENTER"]),
        (
            ["home", "ok"],
            2,
            ["KEY_HOME", 2.0, "KEY_ENTER", 2.0, "KEY_HOME", 2.0, "KEY_ENTER"],
        ),
    ],
)
async def test_remote_delay_between_commands(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, commands, repeats, expected
) -> None:
    """Honor delays between keys, including repeat boundaries, with no end delays."""
    from custom_components.localthings import remote

    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity_id = hass.states.async_all("remote")[0].entity_id
    events = []

    async def send_command(bound, key):
        events.append(key)

    async def sleep(delay):
        events.append(delay)

    monkeypatch.setattr(coordinator, "async_send_command", send_command)
    # Replace only the remote module's sleep, leaving HA's background tasks alone.
    monkeypatch.setattr(remote, "asyncio", SimpleNamespace(sleep=sleep))
    await hass.services.async_call(
        "remote",
        "send_command",
        {
            "entity_id": entity_id,
            "command": commands,
            "num_repeats": repeats,
            "delay_secs": 2.0,
        },
        blocking=True,
    )
    assert events == expected


@pytest.mark.parametrize(
    ("content_input", "source", "track", "title"),
    [
        ("app", "dtv", {}, "Plex"),
        ("app", "dtv", {}, "YouTube"),
        ("dtv", "dtv", {"title": "Episode", "appName": "Streaming app"}, "Episode"),
        ("dtv", "dtv", {"appName": "Streaming app"}, "Streaming app"),
        ("dtv", "HDMI1", {}, None),
    ],
)
async def test_apps_and_hdmi_do_not_inherit_broadcast_metadata(
    hass: HomeAssistant, content_input, source, track, title
) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    coordinator._observe.apply(
        "/sec/tv/contentinfo",
        {"x.com.samsung.tv.input": content_input, "x.com.samsung.tv.title": title or "Old show"},
        source="optimistic",
    )
    coordinator._observe.apply(
        "/sec/tv/inputsource", {"x.com.samsung.tv.currentMode": source}, source="optimistic"
    )
    coordinator._observe.apply("/capability/videoMetaData/main/0", track, source="optimistic")
    player._handle_coordinator_update()
    state = hass.states.get(player.entity_id)
    assert state is not None
    assert state.attributes["media_content_type"] == "video"
    assert state.attributes.get("media_title") == title
    assert "media_channel" not in state.attributes
    assert "media_duration" not in state.attributes
    # An app without a playback position must not inherit the old programme clock.
    assert player._schedule() is None
    if source == "HDMI1":
        # The TV's own player stopped, but the TV shows the HDMI device: on, not idle.
        coordinator._observe.apply(
            "/capability/mediaPlayback/main/0", {"modes": ["stop"]}, source="optimistic"
        )
        assert player.state == "on"
    if not track.get("title"):
        assert "media_position" not in state.attributes

    # Returning to the tuner restores the station and schedule.
    coordinator._observe.apply(
        "/capability/videoMetaData/main/0", {"title": "", "appName": ""}, source="optimistic"
    )
    coordinator._observe.apply(
        "/sec/tv/contentinfo", {"x.com.samsung.tv.input": "dtv"}, source="optimistic"
    )
    coordinator._observe.apply(
        "/sec/tv/inputsource", {"x.com.samsung.tv.currentMode": "dtv"}, source="optimistic"
    )
    assert player.media_channel == "4 - News 24 HD"
    assert player.media_duration == 50 * 60


async def test_playing_progress_has_a_timestamp_from_initial_setup(hass: HomeAssistant) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    session = FakeTvSession()
    session.resources["/capability/videoMetaData/main/0"].update(
        {"title": "Episode", "duration": 600_000}
    )
    session.resources["/capability/mediaPlayback/main/0"].update(
        {"modes": ["play"], "position": 120_000}
    )
    entry = _entry(hass)
    with patch(f"{__name__}.FakeTvSession", return_value=session):
        await _setup_tv(hass, entry)
    player = next(iter(hass.data["media_player"].entities))
    state = hass.states.get(player.entity_id)
    assert state is not None and state.state == MediaPlayerState.PLAYING
    stamp = state.attributes["media_position_updated_at"]
    assert stamp is not None
    assert state.attributes["media_position"] == 120

    # HA's frontend adds elapsed time to this stable sample between device updates.
    later = stamp + timedelta(seconds=30)
    with patch("custom_components.localthings.media_player.dt_util.utcnow", return_value=later):
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator._observe.apply(player._desc.audio_href, {"volume": 25}, source="optimistic")
        player._handle_coordinator_update()
        state = hass.states.get(player.entity_id)
        assert state is not None
        assert state.attributes["media_position_updated_at"] == stamp
        progress = (
            state.attributes["media_position"]
            + (dt_util.utcnow() - state.attributes["media_position_updated_at"]).total_seconds()
        )
        assert progress == 150


async def test_the_remote_and_the_player_share_one_power_path(hass: HomeAssistant) -> None:
    """Both go through AVControl, so the remote's power commands take the
    same lock and camera release as the player's."""
    from unittest.mock import AsyncMock

    from custom_components.localthings.registry.entities import MediaPlayerDesc

    await _setup_tv(hass, _entry(hass))
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    remote = next(iter(hass.data["remote"].entities))
    player = next(iter(hass.data["media_player"].entities))

    with (
        patch.object(coordinator.av, "async_turn_on", AsyncMock()) as turn_on,
        patch.object(coordinator.av, "async_turn_off", AsyncMock()) as turn_off,
    ):
        for entity in (remote, player):
            await entity.async_turn_on()
            await entity.async_turn_off()

    for mock in (turn_on, turn_off):
        assert mock.await_count == 2
        assert all(isinstance(call.args[0].desc, MediaPlayerDesc) for call in mock.await_args_list)


async def test_a_source_can_be_named_by_its_input_id(hass: HomeAssistant) -> None:
    """The TV renames an input after the device on it; the input id stays."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    player = hass.states.async_all("media_player")[0].entity_id
    state = hass.states.get(player)
    assert state is not None
    assert "HDMI1" not in state.attributes["source_list"]

    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": player, "source": "HDMI1"}, blocking=True
    )

    assert session.writes[-1] == (
        ["sec", "tv", "inputsource"],
        {"x.com.samsung.tv.currentMode": "HDMI1"},
    )

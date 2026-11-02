"""HW-S61B soundbar: end-to-end through Home Assistant.

A PSK entry polled flat (no /device/0 anywhere), routed to the soundbar
registry by /oic/d alone, with the media_player entity read off the live
state machine and exercised through its service calls.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PORT,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    CONF_READ_MODE,
    CONF_SERIAL,
    DOMAIN,
    PSK_PROFILE_OWNER,
    READ_MODE_PER_HREF,
    TRANSPORT_DTLS,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.observe import MODE_OBSERVE
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES


def _freeze_polls(coordinator) -> None:
    """Stop sub-polls from overwriting seeded reps.

    Cancelling _subpoll_task isn't enough, because a GET already in flight
    still lands. Clearing the tier lists makes later ticks no-ops; the
    caller must block_till_done once before seeding.
    """
    coordinator._hot_hrefs = []
    coordinator._warm_hrefs = []
    if coordinator._subpoll_task is not None:
        coordinator._subpoll_task.cancel()


MOCK_HOST = "10.0.0.63"
MOCK_PORT = 40294
MOCK_DI = "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31"
MOCK_PSK_IDENTITY = "9f8e7d6c-5b4a-4321-8765-0123456789ab"

# A test constant; the DTLS handshake is faked below.
ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: MOCK_PORT,
    CONF_AUTH_CARRIER: AUTH_PSK,
    CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
    CONF_PSK_IDENTITY: MOCK_PSK_IDENTITY,
    CONF_PSK_KEY: "0123456789abcdef0123456789abcdef",
    "transport": TRANSPORT_DTLS,
    CONF_DEVICE_KEY: MOCK_DI,
    "ocf_device_id": MOCK_DI,
    CONF_SERIAL: MOCK_HOST,
    CONF_MODEL: "HW-S61B",
    CONF_MANUFACTURER: "Samsung Electronics",
    CONF_READ_MODE: READ_MODE_PER_HREF,
}


def _soundbar_resources() -> dict:
    data = json.loads((FIXTURES / "soundbar_s61b_device.json").read_text())
    return parse_device0_batch(data["device0"])


_IDENTITY = DeviceIdentity(
    manufacturer="Samsung Electronics",
    model="HW-TEST",
    name="Test AV",
    serial=None,
    device_id="7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31",
    device_types=("oic.wk.d", "oic.d.networkaudio"),
    raw={
        "/oic/res": [{"href": href} for href in _soundbar_resources()],
        "/oic/p": {"mnfv": "HW-TESTWWB-1000.0", "mnmo": "HW-TEST"},
    },
)


class FakeSoundbarSession:
    """A flat device: /device/0 4.04s, every advertised href answers."""

    supports_observe = True

    def __init__(self) -> None:
        self.resources = _soundbar_resources()
        self.reads: list[str] = []
        self.writes: list[tuple[list[str], dict]] = []
        self.subscribed: list[str] = []

    def subscribe(self, path_segs):
        self.subscribed.append("/" + "/".join(path_segs))
        return b"\x01"

    def refresh_observes(self, paths):
        return None

    def connect(self):
        pass

    def read(self, path, timeout=None):
        href = "/" + "/".join(path)
        self.reads.append(href)
        if list(path) == ["device", "0"]:
            return 0x84, None
        rep = self.resources.get(href)
        return (0x45, dict(rep)) if rep is not None else (0x84, None)

    def write(self, path, body, timeout=None):
        self.writes.append((list(path), body))
        if isinstance(body, dict):
            href = "/" + "/".join(path)
            self.resources.setdefault(href, {}).update(body)
        return 0x44, None

    def pace(self):
        pass

    def diagnostics(self):
        return {}

    def close(self):
        pass


async def _setup_soundbar(hass: HomeAssistant, entry: MockConfigEntry) -> FakeSoundbarSession:
    session = FakeSoundbarSession()
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


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_DI}", version=4
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> LocalThingsCoordinator:
    return hass.data[DOMAIN][entry.entry_id]


async def test_av_boards_never_attempt_observe(hass: HomeAssistant) -> None:
    """No OBSERVE on this board (see by_type/soundbar.py), so it stays "polling"."""
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator.device_type_name == "soundbar"
    assert session.subscribed == []
    assert coordinator._observe.mode != MODE_OBSERVE


async def test_psk_entry_polls_flat(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = _coordinator(hass, entry)

    assert coordinator._flat is True
    assert coordinator._write_settle_s == 6.0
    assert coordinator.device_type_name == "soundbar"
    # /device/0 asked once, on the first poll (it answers 4.04), then per href.
    assert session.reads.count("/device/0") == 1
    # No /oic/sec/* GET ever left the integration, enumeration included.
    assert not any(href.startswith("/oic/sec/") for href in session.reads)
    # The directory the coordinator polls is the one /oic/res advertised,
    # but for the onboarding resources that carry tokens.
    from custom_components.localthings.registry.flat import _SKIP_EXACT

    assert set(_soundbar_resources()) - set(session.reads) == _SKIP_EXACT & set(
        _soundbar_resources()
    )
    assert not _SKIP_EXACT & set(session.reads)


async def test_media_player_state_and_controls(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)

    players = hass.states.async_all("media_player")
    assert len(players) == 1
    player = players[0]
    entity_id = player.entity_id

    assert player.state == MediaPlayerState.IDLE  # powered, modes ["stop"]
    assert player.attributes["volume_level"] == 0.11
    assert player.attributes["is_volume_muted"] is False
    assert player.attributes["source"] == "optical"
    assert player.attributes["source_list"] == ["optical", "bt", "wifiidle"]
    assert player.attributes["sound_mode"] == "standard"

    async def call(service: str, data: dict | None = None) -> None:
        await hass.services.async_call(
            "media_player", service, {"entity_id": entity_id, **(data or {})}, blocking=True
        )

    await call("volume_set", {"volume_level": 0.42})
    await call("volume_mute", {"is_volume_muted": True})
    await call("select_source", {"source": "bt"})
    await call("select_sound_mode", {"sound_mode": "music"})
    await call("media_play")
    await call("media_stop")
    await call("shuffle_set", {"shuffle": True})
    await call("media_next_track")

    # HA seeks to absolute seconds; the device takes a delta. The fixture
    # sits at 120000 ms.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._observe.apply(
        "/capability/mediaPlayback/main/0",
        {"modes": ["play"], "position": 120_000},
        source="optimistic",
    )
    await hass.async_block_till_done()
    await call("media_seek", {"seek_position": 60.0})

    # The optimistic cache holds the rebased absolute ms, not the delta.
    rep = coordinator.resource("/capability/mediaPlayback/main/0")
    assert rep["position"] == 60_000
    assert rep["modes"] == ["play"]
    player = next(iter(hass.data["media_player"].entities))
    assert player.media_position == 60
    await call("media_seek", {"seek_position": 120.0})

    # The toggle goes out as is; the cache gets the mode it toggles to.
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == MediaPlayerState.PLAYING
    await call("media_play_pause")
    assert (
        ["capability", "mediaPlayback", "main", "0"],
        {"modes": ["playpause"]},
    ) in session.writes
    assert coordinator.resource("/capability/mediaPlayback/main/0")["modes"] == ["pause"]

    await call("turn_off")

    assert (["sec", "networkaudio", "audio"], {"volume": 42}) in session.writes
    assert (["sec", "networkaudio", "audio"], {"mute": True}) in session.writes
    assert (["sec", "networkaudio", "mode"], {"mode": "bt"}) in session.writes
    assert (
        ["sec", "networkaudio", "soundmode"],
        {"x.com.samsung.networkaudio.soundmode": "music"},
    ) in session.writes
    assert (["capability", "mediaPlayback", "main", "0"], {"modes": ["play"]}) in session.writes
    assert (["capability", "mediaPlayback", "main", "0"], {"modes": ["stop"]}) in session.writes
    assert (
        ["capability", "mediaPlaybackShuffle", "main", "0"],
        {"mode": "enabled"},
    ) in session.writes
    assert (
        ["capability", "mediaTrackControl", "main", "0"],
        {"modes": ["next"]},
    ) in session.writes
    assert (
        ["capability", "mediaPlayback", "main", "0"],
        {"modes": ["seek"], "position": -60},
    ) in session.writes
    assert (
        ["capability", "mediaPlayback", "main", "0"],
        {"modes": ["seek"], "position": 60},
    ) in session.writes
    assert (["sec", "networkaudio", "switch", "binary"], {"value": False}) in session.writes


async def test_media_position_and_duration_are_seconds_not_milliseconds(
    hass: HomeAssistant,
) -> None:
    """The /capability/* tree reports ms; HA works in seconds."""
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    # Seed the fake itself, so sub-polls return the same reps.
    session.resources["/capability/mediaPlayback/main/0"].update(
        {"modes": ["play"], "position": 133_556}
    )
    session.resources["/capability/audioTrackData/main/0"].update(
        {"title": "One Day We Will Lose Everything", "duration": 225_000}
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Wait for the sub-poll to deliver them.
    for _ in range(50):
        if coordinator.resource("/capability/mediaPlayback/main/0").get("position") == 133_556:
            break
        await asyncio.sleep(0.01)

    assert player.media_position == 133
    assert player.media_duration == 225
    assert player.media_position_updated_at is not None


async def test_state_follows_the_networkaudio_playback_line(
    hass: HomeAssistant,
) -> None:
    """The bar's own playback status wins over /capability's 'stop'."""
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    _freeze_polls(coordinator)
    await hass.async_block_till_done()

    def _seed_status(status: str) -> None:
        coordinator._observe.apply(
            "/sec/networkaudio/playback",
            {"x.com.samsung.networkaudio.status": status},
            source="optimistic",
        )

    _seed_status("PLAYING")
    await hass.async_block_till_done()
    assert (
        coordinator.resource("/sec/networkaudio/playback").get("x.com.samsung.networkaudio.status")
        == "PLAYING"
    )
    assert player.state == MediaPlayerState.PLAYING

    _seed_status("PAUSED")
    for _ in range(500):
        if (
            coordinator.resource("/sec/networkaudio/playback").get(
                "x.com.samsung.networkaudio.status"
            )
            == "PAUSED"
        ):
            break
        await asyncio.sleep(0.01)
    assert (
        coordinator.resource("/sec/networkaudio/playback").get("x.com.samsung.networkaudio.status")
        == "PAUSED"
    )
    assert player.state == MediaPlayerState.PAUSED

    _seed_status("IDLE")
    for _ in range(500):
        if (
            coordinator.resource("/sec/networkaudio/playback").get(
                "x.com.samsung.networkaudio.status"
            )
            == "IDLE"
        ):
            break
        await asyncio.sleep(0.01)
    assert (
        coordinator.resource("/sec/networkaudio/playback").get("x.com.samsung.networkaudio.status")
        == "IDLE"
    )
    assert player.state == MediaPlayerState.IDLE


async def test_airplay_stateless_surface_presents_as_playing(
    hass: HomeAssistant,
) -> None:
    """STOPPED with a live track and an AirPlay sender counts as playing."""
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    _freeze_polls(coordinator)
    await hass.async_block_till_done()

    coordinator._observe.apply(
        "/sec/networkaudio/playback",
        {"x.com.samsung.networkaudio.status": "STOPPED"},
        source="optimistic",
    )
    coordinator._observe.apply(
        "/capability/audioTrackData/main/0",
        {"title": "Stáří", "appName": "airplay-app"},
        source="optimistic",
    )
    coordinator._observe.apply(
        "/sec/networkaudio/soundFrom",
        {
            "x.com.samsung.networkaudio.soundFrom": {
                "connectionType": "WIFI",
                "name": "AirPlay",
                "status": 1,
            }
        },
        source="optimistic",
    )
    await hass.async_block_till_done()

    assert player.state == MediaPlayerState.PLAYING

    # Stream over: the title clears and the sender session drops.
    coordinator._observe.apply(
        "/capability/audioTrackData/main/0",
        {"title": ""},
        source="optimistic",
    )
    coordinator._observe.apply(
        "/sec/networkaudio/soundFrom",
        {
            "x.com.samsung.networkaudio.soundFrom": {
                "connectionType": "WIFI",
                "name": "",
                "status": 0,
            }
        },
        source="optimistic",
    )
    await hass.async_block_till_done()
    assert player.state == MediaPlayerState.IDLE


async def test_device_registry_carries_firmware_but_no_uuid_serial(
    hass: HomeAssistant,
) -> None:
    """Firmware from /oic/p lands on the card; the OCF per-unit UUID is
    NOT a serial (an AV board has none -- resolve_serial's host fallback
    is an address, not identity) and must not surface as one."""
    from homeassistant.helpers import device_registry as dr

    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    device = next(
        row
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if row.model == "HW-TEST"
    )
    assert device.sw_version == "HW-TESTWWB-1000.0"
    assert device.serial_number is None


async def test_standalone_entities_register(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    coordinator = _coordinator(hass, entry)

    keys = {b.desc.key for b in coordinator.bound}
    for expected in ("media_player", "bass", "woofer_level", "firmware", "network_connection"):
        assert expected in keys
    # The media player's surfaces have no standalone duplicates.
    for dup in ("power_switch", "volume", "mute", "input_source", "soundbar_sound_mode"):
        assert dup not in keys


async def test_channel_level_write_keeps_the_other_channels(
    hass: HomeAssistant,
) -> None:
    """Writing one channel trim keeps the other channels in the optimistic cache."""
    from homeassistant.helpers import entity_registry as er

    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)

    ent_reg = er.async_get(hass)
    by_unique = {e.unique_id: e.entity_id for e in ent_reg.entities.values()}
    center = next(eid for uid, eid in by_unique.items() if uid.endswith("channel_level_center"))
    rear = next(eid for uid, eid in by_unique.items() if uid.endswith("channel_level_rear"))
    flag = next(eid for uid, eid in by_unique.items() if uid.endswith("rear_speakers"))

    def _state_of(eid: str) -> str:
        state = hass.states.get(eid)
        assert state is not None, f"{eid} not in the state machine at all"
        return state.state

    # Fixture values before the write (center/side/rear all present at -6/-6/+6).
    assert float(_state_of(center)) == -6
    assert float(_state_of(rear)) == 6
    assert _state_of(flag) == "on"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": rear, "value": 3},
        blocking=True,
    )

    # Inside the settle window only the written channel changes.
    assert float(_state_of(rear)) == 3
    assert float(_state_of(center)) == -6
    assert _state_of(flag) == "on"


async def test_diagnostics_report_the_stored_read_mode(hass: HomeAssistant) -> None:
    from custom_components.localthings.diagnostics import async_get_config_entry_diagnostics

    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["read_mode"] == READ_MODE_PER_HREF


async def test_seek_from_the_beginning_of_a_track(hass: HomeAssistant) -> None:
    """Position 0 with a live track is a real position, and seeking from it works."""
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    _freeze_polls(coordinator)
    await hass.async_block_till_done()

    # The live track is what makes 0 a real position.
    coordinator._observe.apply(
        "/capability/audioTrackData/main/0",
        {"title": "Intro", "artist": "Test"},
        source="optimistic",
    )
    coordinator._observe.apply(
        "/capability/mediaPlayback/main/0",
        {"modes": ["play"], "position": 0},
        source="optimistic",
    )
    await hass.async_block_till_done()
    assert player.media_position == 0

    await hass.services.async_call(
        "media_player",
        "media_seek",
        {"entity_id": player.entity_id, "seek_position": 90.0},
        blocking=True,
    )
    assert (
        ["capability", "mediaPlayback", "main", "0"],
        {"modes": ["seek"], "position": 90},
    ) in session.writes


async def test_position_timestamp_follows_only_the_position_feed(
    hass: HomeAssistant,
) -> None:
    """media_position_updated_at restamps when the position changes, not on a volume write."""
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    async def _wait_cache(href: str, field: str, value) -> None:
        # The sub-poll delivers seeded reps within a tick.
        for _ in range(100):
            if coordinator.resource(href).get(field) == value:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{href} {field} never became {value!r}")

    # Seed what the bar would answer live mid-track.
    session.resources["/capability/mediaPlayback/main/0"].update(
        {"modes": ["play"], "position": 120_000}
    )
    session.resources["/capability/audioTrackData/main/0"].update({"title": "T"})
    await coordinator.async_refresh()
    await _wait_cache("/capability/mediaPlayback/main/0", "position", 120_000)
    for _ in range(100):
        if player.media_position_updated_at is not None:
            break
        await asyncio.sleep(0.01)
    stamp = player.media_position_updated_at
    assert stamp is not None

    # Same position feed, different resource: the stamp must hold.
    session.resources["/sec/networkaudio/audio"].update({"volume": 9, "mute": False})
    await coordinator.async_refresh()
    await _wait_cache("/sec/networkaudio/audio", "volume", 9)
    assert player.media_position == 120
    assert player.media_position_updated_at == stamp

    # A genuinely fresh position sample restamps.
    session.resources["/capability/mediaPlayback/main/0"].update(
        {"modes": ["play"], "position": 121_500}
    )
    await coordinator.async_refresh()
    await _wait_cache("/capability/mediaPlayback/main/0", "position", 121_500)
    for _ in range(100):
        if player.media_position_updated_at > stamp:
            break
        await asyncio.sleep(0.01)
    assert player.media_position == 121
    assert player.media_position_updated_at is not None
    assert player.media_position_updated_at > stamp


async def test_airplay_frozen_feed_shows_no_progress_and_offers_no_seek(
    hass: HomeAssistant,
) -> None:
    """AirPlay/BT: the bar plays (its own status) while /capability's feed
    says stop with a frozen position -- that position must not drive a
    progress bar or a seek."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    _freeze_polls(coordinator)
    await hass.async_block_till_done()

    for href, rep in (
        ("/sec/networkaudio/playback", {"x.com.samsung.networkaudio.status": "PLAYING"}),
        ("/capability/mediaPlayback/main/0", {"modes": ["stop"], "position": 133_556}),
        ("/capability/audioTrackData/main/0", {"title": "Song", "duration": 225_000}),
    ):
        coordinator._observe.apply(href, rep, source="poll")
    await hass.async_block_till_done()

    assert player.state == MediaPlayerState.PLAYING
    assert player.media_position is None
    assert player.media_position_updated_at is None
    assert not player.supported_features & MediaPlayerEntityFeature.SEEK
    writes = len(session.writes)
    await player.async_media_seek(30)
    assert len(session.writes) == writes


async def test_firmware_update_entity_follows_sw_update(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    coordinator = _coordinator(hass, entry)
    _freeze_polls(coordinator)
    await hass.async_block_till_done()

    [update] = hass.states.async_all("update")
    assert update.state == "off"  # noupdate, progress -1
    assert update.attributes["in_progress"] is False

    coordinator._observe.apply(
        "/sec/networkaudio/swUpdate",
        {
            "x.com.samsung.networkaudio.updateStatus": "downloading",
            "x.com.samsung.networkaudio.updateVersion": "1301.9",
            "x.com.samsung.networkaudio.updateProgress": 40,
        },
        source="optimistic",
    )
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    update = hass.states.get(update.entity_id)
    assert update is not None
    assert update.state == "on"
    assert update.attributes["latest_version"] == "1301.9"
    assert update.attributes["in_progress"] is True
    assert update.attributes["update_percentage"] == 40


async def test_the_soundbar_offers_no_play_media(hass: HomeAssistant) -> None:
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    await _setup_soundbar(hass, _mock_entry(hass))
    assert (
        not next(iter(hass.data["media_player"].entities)).supported_features
        & MediaPlayerEntityFeature.PLAY_MEDIA
    )

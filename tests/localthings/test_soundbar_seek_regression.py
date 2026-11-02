"""Seek commands keep the wire delta separate from the optimistic target."""

import asyncio
from datetime import timedelta
from functools import partial
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.media_player import _PROFILES, LocalThingsMediaPlayer
from custom_components.localthings.registry.capabilities import soundbar
from custom_components.localthings.registry.discovery import BoundEntity
from tests.localthings.test_soundbar import ENTRY_DATA


async def _prepared_put(write, path, body, href, **kwargs):
    prepare = kwargs.pop("prepare_body", None)
    if prepare is not None:
        prepared = prepare()
        if prepared is None:
            return False
        body = prepared
    await write(path, body, href, **kwargs)
    return True


@pytest.mark.parametrize("family", ["soundbar"])
@pytest.mark.parametrize("error", [HomeAssistantError("write failed"), asyncio.CancelledError()])
async def test_failed_seek_preserves_position_evidence(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, family, error
) -> None:
    entry = MockConfigEntry(domain="localthings", data=ENTRY_DATA, version=4)
    coordinator = LocalThingsCoordinator(hass, entry)
    write = AsyncMock(side_effect=[error, None])
    monkeypatch.setattr(coordinator, "_async_put", partial(_prepared_put, write))
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    capability = soundbar.MEDIA_PLAYER
    profile = _PROFILES[family]
    bound = BoundEntity(
        href=profile.playback_href, capability=capability, desc=capability.entities[0]
    )
    player = LocalThingsMediaPlayer(coordinator, bound, profile)
    coordinator._observe.apply(
        profile.playback_href, {"position": 120_000, "modes": ["play"]}, source="optimistic"
    )
    now = dt_util.utcnow()
    timestamp = now - timedelta(seconds=10)
    evidence = ("playback", 120_000, True)
    player._position_updated_at, player._last_position_evidence = timestamp, evidence
    with patch("custom_components.localthings.media_player.dt_util.utcnow") as utcnow:
        utcnow.return_value = now
        with pytest.raises(type(error)):
            await player.async_media_seek(180)
        assert player._position_updated_at == timestamp
        assert player._last_position_evidence == evidence
        assert coordinator.resource(profile.playback_href)["position"] == 120_000
        utcnow.return_value = now + timedelta(seconds=2)
        await player.async_media_seek(190)
    assert write.call_args.args[1] == {"modes": ["seek"], "position": 58}


@pytest.mark.parametrize("family", ["soundbar"])
@pytest.mark.parametrize("mode", ["play", "pause"])
@pytest.mark.parametrize("targets", [(180.0, 190.0), (0.0, 10.0)])
async def test_consecutive_seeks_preserve_absolute_targets(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, family, mode, targets
) -> None:
    entry = MockConfigEntry(domain="localthings", data=ENTRY_DATA, version=4)
    coordinator = LocalThingsCoordinator(hass, entry)
    write = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_put", partial(_prepared_put, write))
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    capability = soundbar.MEDIA_PLAYER
    profile = _PROFILES[family]
    bound = BoundEntity(
        href=profile.playback_href, capability=capability, desc=capability.entities[0]
    )
    player = LocalThingsMediaPlayer(coordinator, bound, profile)
    for href, rep in (
        (profile.power_href, {"value": True}),
        (profile.track_hrefs[0], {"title": "Track"}),
        (profile.playback_href, {"position": 120_000, "modes": [mode]}),
    ):
        coordinator._observe.apply(href, rep, source="optimistic")

    now = dt_util.utcnow()
    player._position_updated_at = now - timedelta(seconds=10)
    player._last_position_evidence = ("playback", 120_000, mode == "play")
    current = 130.0 if mode == "play" else 120.0
    with patch("custom_components.localthings.media_player.dt_util.utcnow", return_value=now):
        for target in targets:
            await player.async_media_seek(target)
            assert write.call_args.args[1] == {
                "modes": ["seek"],
                "position": round(target - current),
            }
            cached = coordinator.resource(profile.playback_href)
            assert cached["position"] == round(target * 1000)
            assert cached["modes"] == [mode]
            assert player.media_position == target
            assert player.media_position_updated_at == now
            current = target


@pytest.mark.parametrize("family", ["soundbar"])
async def test_seek_after_resume_ignores_the_paused_time(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, family
) -> None:
    entry = MockConfigEntry(domain="localthings", data=ENTRY_DATA, version=4)
    coordinator = LocalThingsCoordinator(hass, entry)
    write = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_put", partial(_prepared_put, write))
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    capability = soundbar.MEDIA_PLAYER
    profile = _PROFILES[family]
    bound = BoundEntity(
        href=profile.playback_href, capability=capability, desc=capability.entities[0]
    )
    player = LocalThingsMediaPlayer(coordinator, bound, profile)
    monkeypatch.setattr(player, "async_write_ha_state", lambda: None)
    coordinator._observe.apply(profile.power_href, {"value": True}, source="optimistic")
    coordinator._observe.apply(profile.track_hrefs[0], {"title": "Track"}, source="optimistic")

    start = dt_util.utcnow()
    with patch("custom_components.localthings.media_player.dt_util.utcnow") as utcnow:
        utcnow.return_value = start
        coordinator._observe.apply(
            profile.playback_href, {"position": 120_000, "modes": ["pause"]}, source="optimistic"
        )
        player._handle_coordinator_update()

        # Five minutes paused, then resumed at the same position.
        utcnow.return_value = start + timedelta(minutes=5)
        coordinator._observe.apply(
            profile.playback_href, {"position": 120_000, "modes": ["play"]}, source="optimistic"
        )
        player._handle_coordinator_update()
        assert player.media_position_updated_at == start + timedelta(minutes=5)

        await player.async_media_seek(150.0)

    assert write.call_args.args[1] == {"modes": ["seek"], "position": 30}


@pytest.mark.parametrize("family", ["soundbar"])
async def test_overlapping_seeks_wait_for_the_previous_position(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, family
) -> None:
    entry = MockConfigEntry(domain="localthings", data=ENTRY_DATA, version=4)
    coordinator = LocalThingsCoordinator(hass, entry)
    started, finish = asyncio.Event(), asyncio.Event()

    async def put(*args, **kwargs):
        started.set()
        await finish.wait()

    write = AsyncMock(side_effect=put)
    monkeypatch.setattr(coordinator, "_async_put", partial(_prepared_put, write))
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    capability = soundbar.MEDIA_PLAYER
    profile = _PROFILES[family]
    bound = BoundEntity(
        href=profile.playback_href, capability=capability, desc=capability.entities[0]
    )
    player = LocalThingsMediaPlayer(coordinator, bound, profile)
    coordinator._observe.apply(
        profile.playback_href, {"position": 120_000, "modes": ["pause"]}, source="optimistic"
    )
    first = asyncio.create_task(player.async_media_seek(180))
    try:
        await started.wait()
        second = asyncio.create_task(player.async_media_seek(190))
        await asyncio.sleep(0)
        assert write.await_count == 1
    finally:
        finish.set()
        await first
    await second
    assert [call.args[1]["position"] for call in write.await_args_list] == [60, 10]


async def test_seek_does_not_aim_from_a_stopped_feed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """During AirPlay the soundbar reads as playing while its feed is stopped
    and frozen: that position is no base for a seek, so none is sent."""
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.coordinator import LocalThingsCoordinator
    from custom_components.localthings.media_player import _PROFILES, LocalThingsMediaPlayer
    from custom_components.localthings.registry.capabilities import soundbar
    from custom_components.localthings.registry.discovery import BoundEntity
    from tests.localthings.test_soundbar import ENTRY_DATA

    coordinator = LocalThingsCoordinator(hass, MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA))
    sent = AsyncMock()
    monkeypatch.setattr(coordinator, "async_send_command", sent)
    profile = _PROFILES["soundbar"]
    bound = BoundEntity(
        href=profile.playback_href,
        capability=soundbar.MEDIA_PLAYER,
        desc=soundbar.MEDIA_PLAYER.entities[0],
    )
    player = LocalThingsMediaPlayer(coordinator, bound, profile)
    for href, rep in (
        (profile.power_href, {"value": True}),
        (profile.track_hrefs[0], {"title": "AirPlay track"}),
        (profile.playback_href, {"position": 120_000, "modes": ["stop"]}),
        ("/sec/networkaudio/playback", {"x.com.samsung.networkaudio.status": "STOPPED"}),
        ("/sec/networkaudio/soundFrom", {"x.com.samsung.networkaudio.soundFrom": {"status": 1}}),
    ):
        coordinator._observe.apply(href, rep, source="optimistic")
    player._position_updated_at = dt_util.utcnow() - timedelta(minutes=5)
    assert player.state == MediaPlayerState.PLAYING

    await player.async_media_seek(150)

    sent.assert_not_called()

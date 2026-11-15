"""Tuning the TV: play_media with media_type 'channel'.

Measured on the UE50TU7172: POSTing channelNumber to /sec/tv/channel tunes
the TV and answers controlResponse {result: true, errorCode: ok}.
"""

from __future__ import annotations

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaType
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.const import DOMAIN
from tests.localthings.test_soundbar import _mock_entry as _soundbar_entry
from tests.localthings.test_soundbar import _setup_soundbar
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv

_CHANNEL_PATH = ["sec", "tv", "channel"]


def _player(hass: HomeAssistant):
    return next(iter(hass.data["media_player"].entities))


@pytest.mark.parametrize("number", ["1", "12", "5-1", " 7 "])
async def test_play_media_channel_tunes_the_tv(hass: HomeAssistant, number) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = _player(hass)

    assert player.supported_features & MediaPlayerEntityFeature.PLAY_MEDIA
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": player.entity_id,
            "media_content_type": "channel",
            "media_content_id": number,
        },
        blocking=True,
    )

    assert (_CHANNEL_PATH, {"x.com.samsung.tv.channelNumber": number.strip()}) in session.writes


@pytest.mark.parametrize("number", ["", "CT 1", "1-", "-1", "1-2-3"])
async def test_a_channel_that_is_not_a_number_is_refused(hass: HomeAssistant, number) -> None:
    session = await _setup_tv(hass, _entry(hass))

    with pytest.raises(ServiceValidationError) as err:
        await _player(hass).async_play_media(MediaType.CHANNEL, number)
    assert err.value.translation_key == "invalid_channel_number"
    assert not any(path == _CHANNEL_PATH for path, _body in session.writes)


@pytest.mark.parametrize("media_type", [MediaType.MUSIC, MediaType.PLAYLIST, MediaType.EPISODE])
async def test_other_media_types_are_refused(hass: HomeAssistant, media_type) -> None:
    session = await _setup_tv(hass, _entry(hass))

    with pytest.raises(ServiceValidationError) as err:
        await _player(hass).async_play_media(media_type, "https://example.com/a.mp4")
    assert err.value.translation_key == "unsupported_media_type"
    assert not any(path == _CHANNEL_PATH for path, _body in session.writes)


async def test_the_soundbar_offers_no_play_media(hass: HomeAssistant) -> None:
    await _setup_soundbar(hass, _soundbar_entry(hass))

    assert not _player(hass).supported_features & MediaPlayerEntityFeature.PLAY_MEDIA
    assert hass.data[DOMAIN]


async def test_the_old_station_name_is_not_shown_under_the_new_number(
    hass: HomeAssistant,
) -> None:
    await _setup_tv(hass, _entry(hass))
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]

    await _player(hass).async_play_media(MediaType.CHANNEL, "1")

    rep = coordinator.resource("/sec/tv/channel")
    assert rep["x.com.samsung.tv.channelNumber"] == "1"
    assert rep["x.com.samsung.tv.channelName"] == ""


async def test_tuning_waits_for_the_command_before_it(hass: HomeAssistant) -> None:
    """Channels go through the media commands' lock, like every other one."""
    import asyncio

    session = await _setup_tv(hass, _entry(hass))
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]

    async with coordinator.av.command_lock:
        task = hass.async_create_task(_player(hass).async_play_media(MediaType.CHANNEL, "5"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        assert not any(path == _CHANNEL_PATH for path, _body in session.writes)
    await task

    assert (_CHANNEL_PATH, {"x.com.samsung.tv.channelNumber": "5"}) in session.writes
